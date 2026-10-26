"""会话实体：每个会话自持一套完整运行时。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..conf import ToolConfig
from ..infra import (
    EventBus,
    MessageDeltaEvent,
    MessageEndEvent,
    OpenAIProvider,
    SkillTable,
    ToolTable,
)
from ..messages import (
    build_ephemeral_history,
    build_history,
    list_sessions,
    list_workspaces,
)
from ..plugin import Context, Plugin, PluginHost
from ..spec import (
    LLM,
    AgentService,
    Channel,
    CommandService,
    Delta,
    DeltaEnd,
    Error,
    Job,
    Message,
    Messages,
    NotificationUnion,
    PromptService,
    RequestUnion,
    SessionInit,
    SessionView,
    SkillService,
    ToolCallPrepareEvent,
    ToolPrepare,
    ToolService,
    TurnEndEvent,
    UserInput,
)
from .agent import Agent
from .commands import Commands
from .executor import ToolExecutor
from .prompt import Prompts
from .runner import Runner, RunOptions, SessionContext


class SessionState(StrEnum):
    """会话运行状态：同一时刻只允许一个 run。"""

    IDLE = "idle"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CLOSED = "closed"


@dataclass
class SessionRuntime:
    """会话级运行时：每会话一套，由装配方构造后注入 Session。"""

    bus: EventBus
    executor: ToolExecutor
    tool_table: ToolTable
    skill_table: SkillTable
    tool_config: ToolConfig

    @classmethod
    def build(cls, tool_config: ToolConfig | None = None) -> SessionRuntime:
        """按配置构造默认运行时（装配根与测试共用）。"""
        config = tool_config or ToolConfig()
        return cls(
            bus=EventBus(),
            executor=ToolExecutor(config),
            tool_table=ToolTable(),
            skill_table=SkillTable(),
            tool_config=config,
        )


class SessionBusyError(RuntimeError):
    code = "session_busy"


class SessionClosedError(RuntimeError):
    code = "session_closed"


class Session:
    """单会话运行时：自持 bus / agent 循环 / 插件激活。"""

    def __init__(
        self,
        session_id: str,
        history: Messages,
        tool_config: ToolConfig | None = None,
        runtime: SessionRuntime | None = None,
        plugins: list[Plugin] | None = None,
        transport: Channel | None = None,
        provider: LLM | None = None,
        cwd: str | None = None,
        created_at: float | None = None,
        agent: str | None = None,
    ) -> None:
        self.id = session_id
        # 会话元数据：核心不解释键名，由插件按需读写
        self._metadata: dict[str, Any] = {}
        # 创建时间内聚在 Session（管理器/连接仅读取展示）
        self.created_at = created_at if created_at is not None else time.time()
        # 会话工作目录：bash 执行位置 + 沙箱可写区（无则工具不设 cwd）
        self._cwd = cwd
        # 会话级运行时由装配方注入；未注入时按 tool_config 构造默认实现
        runtime = runtime or SessionRuntime.build(tool_config)
        self._runtime = runtime
        self._tool_config = runtime.tool_config
        self._plugins = plugins or []
        self._host: PluginHost | None = None
        self._plugin_ctx: Context | None = None
        self._prompt: PromptService | None = None
        self._commands: Commands | None = None

        # ── 会话级运行时（每会话独立：bus/agent 循环/插件激活）──
        self._bus = runtime.bus  # 会话私有事件总线（插件事件/工具钩子按会话隔离）
        self._agent_runner = Runner(executor=runtime.executor)  # 执行器只交给 Runner
        self._tool_table = runtime.tool_table  # 会话级工具表
        self._skill_table = runtime.skill_table  # 会话级技能表
        # Runner 只发事件，推送给 channel 由会话监听转发（流式增量/结束标记）
        self._bus.on(MessageDeltaEvent, self._on_delta)
        self._bus.on(MessageEndEvent, self._on_message_end)
        self._bus.on(ToolCallPrepareEvent, self._on_tool_prepare)
        self._bus.on(TurnEndEvent, self._on_turn_end)

        # agent 一律按名从 .agent/agents/ 加载（缺省 default），会话内可切换。
        # 管理器缓存已加载的 Agent 实例，供 handoff/subagent 按名查表构造工具。
        self._agents: dict[str, Agent] = {}
        self._agent = self._build_agent(agent)
        # 依赖由装配根注入：模型（协议面）与会话历史（已包投影视图）
        self._provider = provider
        self._history = history
        # 后台作业表：跨轮存活（background 写入 pending，executor 拉起，job_result 读取）
        self._jobs: dict[str, Job] = {}
        # transport：连接端点（attach/detach 切换），唯一 channel 事实源
        self._transport: Channel | None = transport
        self._attached = asyncio.Event()
        if transport is not None:
            self._attached.set()
        # 当前 turn 的通知缓冲：断连期间保留，重连后补发
        self._pending: list[NotificationUnion] = []
        self._replaying = False
        # 单写者：一个会话同时只跑一个 run；忙时直接拒绝，不排队
        self._state = SessionState.IDLE  # 单写者状态机
        self._run_task: asyncio.Task | None = None
        self._cancel_reason: str | None = None

    async def start(self) -> None:
        """启动插件运行时：把服务适配到本会话，再执行插件 apply。"""
        if not self._plugins:
            return
        host = PluginHost(self._bus)
        self._host = host
        self._plugin_ctx = host.root
        self._commands = Commands()
        await host.root.register(CommandService, self._commands)
        await host.root.register(Channel, self)
        await host.root.register(AgentService, Agents(self))
        await host.root.register(SessionView, self.context)
        await host.root.register(ToolService, self._tool_table)
        await host.root.register(PromptService, Prompts())
        await host.root.register(SkillService, self._skill_table)
        await host.launch(
            self._plugins,
            {
                "approval": {"config": self._tool_config.approval},
            },
        )
        self._prompt = host.root.get(PromptService)

    @property
    def context(self) -> SessionContext:
        """会话环境视图（每次现造）：对外访问会话能力的统一入口。"""
        return SessionContext(
            provider=self._provider,
            channel=self,
            history=self._history,
            session_id=self.id,
            jobs=self._jobs,
            tool_table=self._tool_table,  # 工具执行期动态注册通道
            skills=self._skill_table,  # 技能清单（提示词）
            cwd=self._cwd,
            agents=self._agents,  # handoff/subagent 按名查表
            bus=self._bus,  # 会话事件总线（Runner 发事件 + 工具钩子共用）
            history_factory=build_ephemeral_history,  # 子 agent 隔离历史
            metadata=self._metadata,
            prompt=self._prompt,  # 插件提供的动态 system 段
        )

    @property
    def metadata(self) -> dict[str, Any]:
        """会话元数据（只读视图；写入走 update_metadata）。"""
        return dict(self._metadata)

    def update_metadata(self, values: dict[str, Any]) -> None:
        """合并会话元数据；值为 None 表示删除该键。"""
        for key, value in values.items():
            if value is None:
                self._metadata.pop(key, None)
            else:
                self._metadata[key] = value

    def _build_agent(self, name: str | None = None) -> Agent:
        """按名加载 agent 并缓存（工具不经 agent——运行时由插件 runner 提供）。"""
        name = name or "default"
        if name not in self._agents:
            self._agents[name] = Agent.load(name)
        return self._agents[name]

    @property
    def attached(self) -> bool:
        """是否有活跃连接的 transport 绑定。"""
        return self._transport is not None

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def agent_name(self) -> str | None:
        return self._agent.name

    @property
    def transport(self) -> Channel | None:
        return self._transport

    async def conversation_history(self) -> list[Message]:
        """客户端可见的完整对话历史（含工具调用与结果，不含 system）。"""
        total = await self._history.count()
        messages = await self._history.query(offset=0, limit=total)
        return [m for m in messages if m.role != "system"]

    @property
    def agent(self) -> Agent:
        """当前 agent。"""
        return self._agent

    def get_agent(self, name: str) -> Agent:
        """按名加载并缓存 agent。"""
        return self._build_agent(name)

    def set_agent(self, name: str = "default") -> None:
        """按名切换 agent（重新加载 frontmatter）；缺省用 default。"""
        self._agent = self._build_agent(name)

    async def notify(self, notification: NotificationUnion) -> None:
        """缓冲当前 turn 的通知并推给当前 transport。"""
        self._pending.append(notification)
        if self._replaying:
            return
        if self._transport is not None:
            await self._transport.notify(notification)

    async def call(self, request: RequestUnion) -> Any:
        """向当前连接发起交互；断连时等待重连，连接失败则重试。"""
        while True:
            if self._state is SessionState.CLOSED:
                raise SessionClosedError(f"session '{self.id}' is closed")
            transport = self._transport
            if transport is not None:
                try:
                    return await transport.call(request)
                except ConnectionError:
                    if self._state is SessionState.CLOSED:
                        raise
                    if self._transport is transport:
                        self._attached.clear()
            await self._attached.wait()

    async def replay(self, transport: Channel) -> None:
        """把当前 turn 尚未确认的通知补发给新连接。"""
        pending, self._pending = self._pending, []
        self._replaying = True
        try:
            for notification in pending:
                await transport.notify(notification)
        finally:
            self._replaying = False
            rest, self._pending = self._pending, []
            for notification in rest:
                await transport.notify(notification)

    async def _on_delta(self, evt: MessageDeltaEvent) -> None:
        """流式增量事件 → 当前连接；断连期间进入缓冲。"""
        await self.notify(Delta(delta=evt.delta))

    async def _on_tool_prepare(self, evt: ToolCallPrepareEvent) -> None:
        """模型开始生成工具参数 → 先告诉客户端，工具尚未执行。"""
        await self.notify(ToolPrepare(tool_name=evt.tool_name))

    async def _on_turn_end(self, evt: TurnEndEvent) -> None:
        """一个 turn 已落库，清掉该 turn 的实时通知缓冲。"""
        self._pending.clear()

    async def _on_message_end(self, evt: MessageEndEvent) -> None:
        """run 终态 → DeltaEnd；reason 为机器可读终态原因（正常结束为 None）。"""
        await self.notify(DeltaEnd(reason=evt.reason))

    def attach(self, transport: Channel) -> None:
        """绑定新 transport（重连/接管）：换 channel 端点。"""
        if self._state is SessionState.CLOSED:
            raise SessionClosedError(f"session '{self.id}' is closed")
        self._transport = transport
        self._attached.set()

    def detach(self, transport: Channel | None = None) -> bool:
        """解除 transport；给定旧端点时只解除当前端点，防止误伤新连接。"""
        if transport is not None and transport is not self._transport:
            return False
        self._transport = None
        self._attached.clear()
        return True

    def submit(self, user_input: UserInput) -> asyncio.Task:
        """提交一次 run 并立即返回；忙时同步抛 SessionBusyError。"""
        if self._state is SessionState.CLOSED:
            raise SessionClosedError(f"session '{self.id}' is closed")
        if self._transport is None:
            raise SessionClosedError(f"session '{self.id}' is detached, attach first")
        if self._state in (SessionState.RUNNING, SessionState.CANCELLING):
            raise SessionBusyError(f"session '{self.id}' is busy ({self._state})")

        content = user_input.content or ""
        self._pending.clear()
        self._state = SessionState.RUNNING
        self._cancel_reason = None
        task = asyncio.create_task(self._run(content))
        self._run_task = task
        return task

    async def _run(self, content: str) -> None:
        """执行一次 run；异常回报当前 transport，结束后恢复 idle。"""
        try:
            await self._execute(content)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._report_error(exc)
        finally:
            self._pending.clear()
            if self._run_task is asyncio.current_task():
                self._run_task = None
            if self._state is not SessionState.CLOSED:
                self._state = SessionState.IDLE

    async def _report_error(self, exc: Exception) -> None:
        """把 run 异常发给当前连接；断连期间进入缓冲。"""
        try:
            await self.notify(
                Error(
                    message=f"处理失败: {exc}",
                    code=getattr(exc, "code", "error"),
                )
            )
        except Exception:
            pass

    async def handle(self, user_input: UserInput) -> None:
        """提交并等待一次 run；忙时抛 SessionBusyError，不排队。"""
        task = self.submit(user_input)
        try:
            await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _execute(self, content: str) -> None:
        try:
            # 命令执行上下文：暴露 transport（推送/提问）、事件总线与会话状态
            # （messages/provider），命令 handler 按需自取。
            if self._commands is not None and self._plugin_ctx is not None:
                result = await self._commands.execute(content, self._plugin_ctx)
                if result is not None:
                    if result.text:
                        await self.notify(Delta(delta=result.text))
                    await self.notify(
                        DeltaEnd(reason=None if result.kind == "success" else result.kind)
                    )
                    return
            await self._agent_runner.run(
                self._agent,
                content,
                env=self.context,
                options=RunOptions(stream=True),
            )
        except asyncio.CancelledError:
            # 主动取消：先发终态再正常返回，调用方不必处理 CancelledError。
            if self._state is SessionState.CANCELLING:
                await self._bus.emit(
                    MessageEndEvent(
                        output=None,
                        session_id=self.id,
                        reason=self._cancel_reason or "cancelled",
                    )
                )
                return
            raise

    async def cancel(self, reason: str = "cancelled") -> bool:
        """取消当前 run；返回是否真的取消了。"""
        task = self._run_task
        if task is None or task.done():
            return False
        self._state = SessionState.CANCELLING
        self._cancel_reason = reason
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if self._state is not SessionState.CLOSED:
            self._state = SessionState.IDLE
        return True

    async def close(self) -> None:
        """真正销毁：取消当前 run / 后台作业，关消息存储。"""
        if self._state is SessionState.CLOSED:
            return
        await self.cancel(reason="session closed")
        self._state = SessionState.CLOSED
        self._attached.set()
        self._pending.clear()
        pending = [
            j.task
            for j in self._jobs.values()
            if j.task is not None and not j.task.done()
        ]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._jobs.clear()
        if self._host is not None:
            await self._host.dispose()
            self._host = None
        await self._history.close()


class SessionManager:
    """进程级会话管理器 + 工厂：session_id → Session。"""

    def __init__(
        self,
        tool_config: ToolConfig | None = None,
        plugins: list[Plugin] | None = None,
        provider: LLM | None = None,
        storage: str = "file",
        path: str | None = None,
    ) -> None:
        self._tool_config = tool_config
        self._plugins = plugins or []
        self._provider = provider
        self._storage = storage
        self._path = path
        self._sessions: dict[str, Session] = {}

    def create(
        self,
        conn: SessionInit,
        plugin_names: list[str] | None = None,
    ) -> Session:
        """新建会话并注册。"""
        plugins = self.select_plugins(plugin_names)
        model_cfg = conn.model
        provider = (
            OpenAIProvider(
                api_key=model_cfg.api_key,
                base_url=model_cfg.base_url,
                model=model_cfg.model,
            )
            if model_cfg is not None
            else self._provider
        )
        session = Session(
            session_id=conn.session_id,
            history=build_history(
                self._storage,
                self._path,
                conn.session_id,
                cwd=conn.cwd,
            ),
            runtime=SessionRuntime.build(self._tool_config),
            plugins=plugins,
            provider=provider,
            cwd=conn.cwd,  # 客户端工作目录（连接握手携带）
            agent=conn.agent,  # 客户端指定 agent（None = default）
        )
        session.attach(conn)
        self.register(session)
        return session

    def select_plugins(self, plugin_names: list[str] | None) -> list[Plugin]:
        """从插件声明池按名单挑选（默认全部）。"""
        if plugin_names is None:
            return list(self._plugins)
        wanted = set(plugin_names)
        return [plugin for plugin in self._plugins if plugin.name in wanted]

    def register(self, session: Session) -> None:
        """注册新会话。同 id 已存在则报错（重连走 attach，不重建）。"""
        if session.id in self._sessions:
            raise ValueError(f"session '{session.id}' already registered")
        self._sessions[session.id] = session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> Session | None:
        """从管理器移除（不 close，调用方决定是否销毁）。"""
        return self._sessions.pop(session_id, None)

    def list(self) -> list[dict]:
        """列出全部会话：id、attached 状态、创建时间。"""
        return [
            {
                "session_id": sid,
                "attached": s.attached,
                "created_at": s.created_at,
                "age_seconds": time.time() - s.created_at,
            }
            for sid, s in self._sessions.items()
        ]

    def list_workspaces(self) -> list[dict]:
        """已登记的工作目录（file 后端）；其他后端暂不提供历史目录。"""
        if self._storage != "file":
            return []
        return list_workspaces(self._path)

    def list_sessions(self, cwd: str) -> list[dict]:
        """某个工作目录下的历史会话（file 后端），按最近更新倒序。"""
        if self._storage != "file" or not cwd:
            return []
        return list_sessions(cwd, self._path)

    async def close(self) -> None:
        """销毁全部会话（服务端停机）。"""
        for s in self._sessions.values():
            try:
                await s.close()
            except Exception:
                pass
        self._sessions.clear()


class Agents:
    """AgentService：当前 agent 与按名加载（会话视图）。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def current(self) -> Agent:
        return self._session.agent

    def get(self, name: str) -> Agent:
        return self._session.get_agent(name)
