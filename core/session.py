"""会话实体：每个会话自持一套完整运行时。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..conf import TimeoutConfig
from ..infra import (
    EventBus,
    Skills,
    Tools,
)
from ..messages import (
    build_ephemeral_history,
)
from ..plugin import Plugin, PluginHost
from ..spec import (
    LLM,
    AgentService,
    Channel,
    CommandService,
    Delta,
    DeltaEnd,
    Error,
    ExecutorService,
    Job,
    Message,
    MessageEndEvent,
    Messages,
    NotificationUnion,
    PromptService,
    RequestUnion,
    SessionView,
    SkillService,
    ToolService,
    TurnEndEvent,
    UserInput,
)
from .agent import Agent
from .builtins import channel_plugin, executor_plugin
from .command import Commands
from .link import SessionClosedError, SessionLink
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
    tools: Tools
    skills: Skills

    @classmethod
    def build(cls) -> SessionRuntime:
        """构造默认运行时（装配根与测试共用）。"""
        return cls(bus=EventBus(), tools=Tools(), skills=Skills())


class SessionBusyError(RuntimeError):
    code = "session_busy"


class Session:
    """单会话运行时：自持 bus / agent 循环 / 插件激活。"""

    def __init__(
        self,
        session_id: str,
        history: Messages,
        timeout: TimeoutConfig | None = None,
        runtime: SessionRuntime | None = None,
        plugins: list[Plugin] | None = None,
        plugin_configs: dict[str, dict[str, Any]] | None = None,
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
        # 会话级运行时由装配方注入；tool_config 只用于配置内置插件
        runtime = runtime or SessionRuntime.build()
        self._runtime = runtime
        self._timeout = timeout or TimeoutConfig()
        self._plugins: list[Plugin] = plugins or []
        self._plugin_configs = plugin_configs or {}
        self._host: PluginHost | None = None
        self._prompt: PromptService | None = None
        self._commands: Commands | None = None

        # ── 会话级运行时（每会话独立：bus/agent 循环/插件激活）──
        self._bus = runtime.bus  # 会话私有事件总线（插件事件/工具钩子按会话隔离）

        self._agent_runner = Runner()  # 执行器由 executor 插件提供，run 时经 context 注入
        self._tools = runtime.tools  # 会话级工具表
        self._skills = runtime.skills  # 会话级技能表
        self._executor: ExecutorService | None = None
        # 断线补发缓冲由 transport 自己维护；展示映射见 channel 插件
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
        # 连接端点：transport / 断线缓冲 / 重放
        self._link = SessionLink(session_id, transport)
        # 单写者：一个会话同时只跑一个 run；忙时直接拒绝，不排队
        self._state = SessionState.IDLE  # 单写者状态机
        self._run_task: asyncio.Task | None = None
        self._cancel_reason: str | None = None

    async def start(self) -> None:
        """启动插件运行时：注册会话服务，再执行内置插件与用户插件。"""
        from .agents import Agents  # 局部导入：Agents 反向依赖 Session

        host = PluginHost(self._bus)
        self._host = host
        self._commands = Commands()
        await host.root.register(CommandService, self._commands)
        await host.root.register(Channel, self)
        await host.root.register(AgentService, Agents(self))
        await host.root.register(SessionView, self.context)
        await host.root.register(ToolService, self._tools)
        await host.root.register(PromptService, Prompts())
        await host.root.register(SkillService, self._skills)
        plugins = list(self._plugins)
        for builtin in (channel_plugin, executor_plugin):
            if all(p.name != builtin.name for p in plugins):
                plugins.insert(0, builtin)  # 默认实现；同名插件可替换
        configs = {"executor": {"timeout": self._timeout.model_dump()}}
        configs.update(self._plugin_configs)
        await host.launch(plugins, configs)
        self._executor = host.root.get(ExecutorService)
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
            tools=self._tools,  # 工具执行期动态注册通道
            skills=self._skills,  # 技能清单（提示词）
            executor=self._executor,  # 工具执行器（executor 插件）
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
        return self._link.attached

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def agent_name(self) -> str | None:
        return self._agent.name

    @property
    def transport(self) -> Channel | None:
        return self._link.transport

    async def conversation_history(self) -> list[Message]:
        """客户端可见的完整对话历史（含工具调用与结果，不含 system）。"""
        total = await self._history.count()
        messages = await self._history.query(offset=0, limit=total)
        return [m for m in messages if m.role != "system"]

    @property
    def agent(self) -> Agent:
        """当前 agent。"""
        return self._agent

    def set_agent(self, name: str = "default") -> None:
        """按名切换 agent（重新加载 frontmatter）；缺省用 default。"""
        self._agent = self._build_agent(name)

    async def notify(self, notification: NotificationUnion) -> None:
        """缓冲当前 turn 的通知并推给当前 transport。"""
        await self._link.notify(notification)

    async def call(self, request: RequestUnion) -> Any:
        """向当前连接发起交互；断连时等待重连，连接失败则重试。"""
        return await self._link.call(request)

    async def replay(self, transport: Channel) -> None:
        """把当前 turn 尚未确认的通知补发给新连接。"""
        await self._link.replay(transport)

    async def _on_turn_end(self, _evt: TurnEndEvent) -> None:
        """一个 turn 已落库，清掉该 turn 的实时通知缓冲。"""
        self._link.clear_pending()

    def attach(self, transport: Channel) -> None:
        """绑定新 transport（重连/接管）：换 channel 端点。"""
        self._link.attach(transport)

    def detach(self, transport: Channel | None = None) -> bool:
        """解除 transport；给定旧端点时只解除当前端点，防止误伤新连接。"""
        return self._link.detach(transport)

    def submit(self, user_input: UserInput) -> asyncio.Task:
        """提交一次 run 并立即返回；忙时同步抛 SessionBusyError。"""
        if self._state is SessionState.CLOSED:
            raise SessionClosedError(f"session '{self.id}' is closed")
        if self._link.transport is None:
            raise SessionClosedError(f"session '{self.id}' is detached, attach first")
        if self._state in (SessionState.RUNNING, SessionState.CANCELLING):
            raise SessionBusyError(f"session '{self.id}' is busy ({self._state})")

        content = user_input.content or ""
        self._link.clear_pending()
        self._state = SessionState.RUNNING
        self._cancel_reason = None
        task = asyncio.create_task(self._run(content))
        self._run_task = task
        return task

    async def _run(self, content: str) -> None:
        """执行一次 run；异常回报当前 transport，结束后恢复 idle。"""
        try:
            await self._dispatch(content)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._report_error(exc)
        finally:
            self._link.clear_pending()
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

    async def _dispatch(self, content: str) -> None:
        try:
            # 命令执行上下文：暴露 transport（推送/提问）、事件总线与会话状态
            # （messages/provider），命令 handler 按需自取。
            if self._commands is not None and self._host is not None:
                result = await self._commands.execute(content, self._host.root)
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

    async def unload_plugin(self, name: str) -> bool:
        """中途卸载插件：其工具/技能/事件订阅随之回收。"""
        if self._host is None:
            return False
        return await self._host.unload(name)

    async def close(self) -> None:
        """真正销毁：取消当前 run / 后台作业，关消息存储。"""
        if self._state is SessionState.CLOSED:
            return
        await self.cancel(reason="session closed")
        self._state = SessionState.CLOSED
        self._link.close()
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
