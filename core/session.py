"""会话实体：每个会话自持一套完整运行时。

会话 = 独立边界：自己的事件总线(bus)、工具执行器(executor)、插件激活
(PluginRunner)、agent 循环(Runner)。插件声明(PluginManager 加载的
Plugin)跨会话共享，但"激活到哪个会话"由本会话的 PluginRunner 决定
——不同会话可激活不同插件组合。

依赖全部由装配根注入（messages / provider / plugins），Session 不自行
new 具体实现——换后端或测试替换替身都无需改本模块。

会话能力与状态的统一访问点是 context(SessionContext)：channel（attach/
detach 切换）、jobs（后台作业表）、plugin_runner（工具/技能源）都在其上。

生命周期：transport 可切换（attach/detach）。连接断开时 detach 保留
messages（状态跨连接存活，供重连恢复）；真正销毁走 close()。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..conf import ToolConfig
from ..infra import (
    CommandContext,
    EventBus,
    HookVerdict,
    MessageDeltaEvent,
    MessageEndEvent,
    OpenAIProvider,
    PluginDeclaration,
    PluginRunner,
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
    Channel,
    Delta,
    DeltaEnd,
    Error,
    Job,
    Message,
    Messages,
    NotificationUnion,
    PreExecuteToolEvent,
    PromptService,
    RequestUnion,
    SessionInit,
    SkillService,
    ToolCallPrepareEvent,
    ToolExecutionStartEvent,
    ToolPrepare,
    ToolService,
    TurnEndEvent,
    UserInput,
)
from .agent import Agent
from .executor import ToolExecutor
from .plugins import SessionPromptService, SessionSkillService, SessionToolService
from .runner import Runner, RunOptions, SessionContext


class SessionBusyError(RuntimeError):
    code = "session_busy"


class SessionClosedError(RuntimeError):
    code = "session_closed"


class Session:
    """单会话运行时：自持 bus / agent 循环 / 插件激活。

    transport 由 attach() 注入（通常为某连接的 SessionConn），作为会话的
    channel 端点直接使用（session_id 补全在连接内完成）。能力与状态统一经
    self.context（SessionContext）访问。
    """

    def __init__(
        self,
        session_id: str,
        history: Messages,
        tool_config: ToolConfig | None = None,
        declarations: list[PluginDeclaration] | None = None,
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
        self._tool_config = tool_config or ToolConfig()
        self._plugins = plugins or []
        self._host: PluginHost | None = None
        self._plugin_ctx: Context | None = None
        self._prompt: PromptService | None = None

        # ── 会话级运行时（每会话独立：bus/agent 循环/插件激活）──
        self._bus = EventBus()  # 会话私有事件总线（插件事件/工具钩子按会话隔离）
        # 工具执行器按会话实例化、只交给 Runner（Session 不持有）；
        executor = ToolExecutor(self._tool_config)
        self._agent_runner = Runner(executor=executor)
        self._plugin_runner = PluginRunner(
            self._bus
        )  # 会话级插件激活（含工具表/命令表）
        if declarations:
            self._plugin_runner.activate(*declarations)  # 按会话选择激活旧式声明
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
        self._state = "idle"  # idle / running / cancelling / closed
        self._run_task: asyncio.Task | None = None
        self._cancel_reason: str | None = None

    async def start(self) -> None:
        """启动插件运行时：把服务适配到本会话，再执行插件 apply。"""
        if not self._plugins:
            return
        host = PluginHost()
        self._host = host
        self._plugin_ctx = host.root
        await host.root.register(
            ToolService, SessionToolService(self._plugin_runner, self._tool_config)
        )
        await host.root.register(PromptService, SessionPromptService())
        await host.root.register(SkillService, SessionSkillService(self._plugin_runner))
        # 新插件的事件总线桥接：工具预执行裁决回落到旧 executor 的 HookVerdict
        self._bus.on(ToolExecutionStartEvent, self._plugin_tool_gate)
        await host.launch(
            self._plugins,
            {
                "approval": {"config": self._tool_config.approval},
            },
        )
        self._prompt = host.root.get(PromptService)

    async def _plugin_tool_gate(self, evt: ToolExecutionStartEvent) -> HookVerdict | None:
        """把新插件总线的 PreExecuteToolEvent 裁决桥接给旧 ToolExecutor。"""
        if self._plugin_ctx is None:
            return None
        verdict = await self._plugin_ctx.bail(
            PreExecuteToolEvent(
                name=evt.tool_name,
                arguments=evt.arguments,
                tool_call_id=evt.tool_call_id,
            )
        )
        if isinstance(verdict, HookVerdict):
            return verdict
        if isinstance(verdict, dict):
            if "block" in verdict:
                return HookVerdict(block=verdict.get("block") or "blocked")
            if isinstance(verdict.get("arguments"), dict):
                return HookVerdict(arguments=verdict["arguments"])
        return None

    @property
    def context(self) -> SessionContext:
        """会话环境视图（每次现造）：对外访问会话能力的统一入口。

        携带当前 transport / 作业表 / 插件激活层；runner.run 直接取此环境。
        """
        return SessionContext(
            provider=self._provider,
            channel=self,
            history=self._history,
            session_id=self.id,
            jobs=self._jobs,
            plugin_runner=self._plugin_runner,  # 工具执行期动态注册通道
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
    def state(self) -> str:
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

    def set_agent(self, name: str = "default") -> None:
        """按名切换 agent（重新加载 frontmatter）；缺省用 default。

        历史保留，仅换 agent 配置——同一会话可随时切换。
        """
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
            if self._state == "closed":
                raise SessionClosedError(f"session '{self.id}' is closed")
            transport = self._transport
            if transport is not None:
                try:
                    return await transport.call(request)
                except ConnectionError:
                    if self._state == "closed":
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
        """run 终态 → DeltaEnd；reason 为机器可读终态原因（正常结束为 None）。

        MessageEndEvent 只在 run 结束时发出（正常结束 / max_turns / 取消），
        因此无论 output 是否为空都必须推送 DeltaEnd——否则客户端会一直等待
        结束信号（Web 停在"生成中"、CLI 不再出现提示符）。
        """
        await self.notify(DeltaEnd(reason=evt.reason))

    def attach(self, transport: Channel) -> None:
        """绑定新 transport（重连/接管）：换 channel 端点。"""
        if self._state == "closed":
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
        if self._state == "closed":
            raise SessionClosedError(f"session '{self.id}' is closed")
        if self._transport is None:
            raise SessionClosedError(f"session '{self.id}' is detached, attach first")
        if self._state in ("running", "cancelling"):
            raise SessionBusyError(f"session '{self.id}' is busy ({self._state})")

        content = user_input.content or ""
        self._pending.clear()
        self._state = "running"
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
            if self._state != "closed":
                self._state = "idle"

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
            if await self._plugin_runner.dispatch(
                content,
                CommandContext(
                    channel=self,
                    bus=self._bus,
                    messages=self._history,
                    provider=self._provider,
                ),
            ):
                return
            await self._agent_runner.run(
                self._agent,
                content,
                env=self.context,
                options=RunOptions(stream=True),
            )
        except asyncio.CancelledError:
            # 主动取消：先发终态再正常返回，调用方不必处理 CancelledError。
            if self._state == "cancelling":
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
        self._state = "cancelling"
        self._cancel_reason = reason
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if self._state != "closed":
            self._state = "idle"
        return True

    async def close(self) -> None:
        """真正销毁：取消当前 run / 后台作业，关消息存储。"""
        if self._state == "closed":
            return
        await self.cancel(reason="session closed")
        self._state = "closed"
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
    """进程级会话管理器 + 工厂：session_id → Session。

    连接断开只 detach（保留状态），会话仍在 manager 中可被重连 attach；
    显式 remove/close 才真正销毁。附加元数据（创建时间等）供列表展示。

    agent 由 Session 内部按名 Agent.load；本类是会话装配根：按配置构造
    历史存储与模型（协议面），create() 组装并登记 Session。
    """

    def __init__(
        self,
        tool_config: ToolConfig | None = None,
        declarations: list[PluginDeclaration] | None = None,
        plugins: list[Plugin] | None = None,
        provider: LLM | None = None,
        storage: str = "file",
        path: str | None = None,
    ) -> None:
        self._tool_config = tool_config
        self._declarations = declarations or []  # 旧式插件声明池（兼容）
        self._plugins = plugins or []  # 新式插件声明池
        self._provider = provider
        self._storage = storage
        self._path = path
        self._sessions: dict[str, Session] = {}

    def create(
        self,
        conn: SessionInit,
        plugin_names: list[str] | None = None,
    ) -> Session:
        """新建会话并注册。

        plugin_names=None → 激活插件池全部可用声明；给定名单 → 只激活命中的
        （跳过加载失败的声明）。模型接入：客户端随握手提供则为本会话新建
        provider（可指向不同端点），否则沿用进程默认。历史存储按 storage
        配置构造（投影包装），与 provider 一并注入 Session。
        """
        declarations = self.select_declarations(plugin_names)
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
            tool_config=self._tool_config,
            declarations=declarations,
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

    def select_declarations(self, plugin_names: list[str] | None) -> list[PluginDeclaration]:
        """从旧式插件声明池按名单挑选（默认全部可用声明）。"""
        pool = [e for e in self._declarations if e.error is None]
        if plugin_names is None:
            return pool
        wanted = set(plugin_names)
        return [e for e in pool if e.name in wanted]

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
