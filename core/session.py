"""会话实体：每个会话自持一套完整运行时。

会话 = 独立边界：自己的事件总线(bus)、工具执行器(executor)、扩展激活
(ExtensionRunner)、agent 循环(Runner)。扩展声明(ExtensionRegistry 加载的
Extension)跨会话共享，但"激活到哪个会话"由本会话的 ExtensionRunner 决定
——不同会话可激活不同扩展组合。

会话能力与状态的统一访问点是 context(SessionContext)：channel（attach/
detach 切换）、jobs（后台作业表）、ext_runner（工具/技能源）都在其上。

生命周期：transport 可切换（attach/detach）。连接断开时 detach 保留
messages（状态跨连接存活，供重连恢复）；真正销毁走 close()。
"""

import asyncio
import time
from typing import Any

from .agent import Agent
from .runner import Runner, RunOptions, SessionContext
from ..channel import Channel
from ..conf import ToolConfig
from ..infra import (
    CommandContext,
    Event,
    EventBus,
    Extension,
    ExtensionRegistry,
    ExtensionRunner,
    Job,
    OpenAIProvider,
)
from ..messages import Messages, InMemoryMessages, SQLiteMessages
from ..schemas import Delta, DeltaEnd, UserInput
from .executor import ToolExecutor


class Session:
    """单会话运行时：自持 bus / agent 循环 / 扩展激活。

    transport 由 attach() 注入（通常为某连接的 CLIConn），作为会话的
    channel 端点直接使用（chat_id 补全在连接内完成）。能力与状态统一经
    self.context（SessionContext）访问。
    """

    def __init__(
        self,
        session_id: str,
        tool_config: ToolConfig | None = None,
        extensions: list[Extension] | None = None,
        transport: Channel | None = None,
        provider: OpenAIProvider | None = None,
        storage: str = "sqlite",
        db_path: str = "data/session.db",
        created_at: float | None = None,
    ) -> None:
        self.id = session_id
        # 创建时间内聚在 Session（注册表/连接仅读取展示）
        self.created_at = created_at if created_at is not None else time.time()

        # ── 会话级运行时（每会话独立：bus/agent 循环/扩展激活）──
        self._bus = EventBus()  # 会话私有事件总线（扩展事件/工具钩子按会话隔离）
        # 工具执行器按会话实例化、只交给 Runner（Session 不持有）
        executor = ToolExecutor(tool_config or ToolConfig())
        self._agent_runner = Runner(executor=executor, bus=self._bus)
        self._ext_runner = ExtensionRunner(
            self._bus
        )  # 会话级扩展激活（含工具表/命令表）
        if extensions:
            self._ext_runner.activate(*extensions)  # 按会话选择激活扩展
        # Runner 只发事件，推送给 channel 由会话监听转发（流式增量/结束标记）
        self._bus.on(Event.MESSAGE_DELTA, self._on_delta)
        self._bus.on(Event.MESSAGE_END, self._on_message_end)

        # agent 一律按名从 .agent/agents/ 加载（缺省 default），会话内可切换
        self._agent = self._build_agent()
        self._provider = provider
        # 会话状态：历史（每会话隔离）
        if storage == "memory":
            self._messages: Messages = InMemoryMessages()
        else:
            self._messages = SQLiteMessages(db_path=db_path, session_id=session_id)
        # 后台作业表：跨轮存活（background 写入 pending，executor 拉起，job_result 读取）
        self._jobs: dict[str, Job] = {}
        # transport：连接端点（attach/detach 切换），唯一 channel 事实源
        self._transport: Channel | None = transport

    @property
    def context(self) -> SessionContext:
        """会话环境视图（每次现造）：对外访问会话能力的统一入口。

        携带当前 transport / 作业表 / 扩展激活层；runner.run 直接取此环境。
        """
        return SessionContext(
            provider=self._provider,
            channel=self._transport,
            messages=self._messages,
            session_id=self.id,
            jobs=self._jobs,
            ext_runner=self._ext_runner,  # 工具执行期动态注册通道
        )

    def _build_agent(self, name: str | None = None) -> Agent:
        """按名加载 agent（工具不经 agent——运行时由扩展 runner 提供）。"""
        return Agent.load(name)

    @property
    def attached(self) -> bool:
        """是否有活跃连接的 transport 绑定。"""
        return self._transport is not None

    def set_agent(self, name: str = "default") -> None:
        """按名切换 agent（重新加载 frontmatter）；缺省用 default。

        历史保留，仅换 agent 配置——同一会话可随时切换。
        """
        self._agent = self._build_agent(name)

    async def _on_delta(self, delta: str) -> None:
        """流式增量事件 → 推给当前 transport（无 transport 则丢弃）。"""
        if self._transport is not None:
            await self._transport.notify(Delta(delta=delta))

    async def _on_message_end(self, output: Any = None, **_: Any) -> None:
        """消息结束事件 → 文本输出完成时发 DeltaEnd（工具轮不发）。"""
        if output is not None and self._transport is not None:
            await self._transport.notify(DeltaEnd())

    def attach(self, transport: Channel) -> None:
        """绑定新 transport（重连/接管）：换 channel 端点。"""
        self._transport = transport

    def detach(self) -> None:
        """解除 transport：保留 messages 状态，会话仍可被 attach 恢复。"""
        self._transport = None

    async def handle(self, user_input: UserInput) -> None:
        if self._transport is None:
            raise RuntimeError(f"session '{self.id}' is detached, attach first")
        content = user_input.content or ""
        # 命令执行上下文：暴露 transport（推送/提问）与事件总线，用法自决
        if await self._ext_runner.dispatch(
            content, CommandContext(channel=self._transport, bus=self._bus)
        ):
            return
        await self._agent_runner.run(
            self._agent,
            content,
            env=self.context,
            options=RunOptions(streamed=True),
        )

    async def close(self) -> None:
        """真正销毁：取消未完成的后台作业，关消息存储。"""
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
        await self._messages.close()


class SessionRegistry:
    """进程级会话注册表 + 工厂：session_id → Session。

    连接断开只 detach（保留状态），会话仍在 registry 中可被重连 attach；
    显式 remove/close 才真正销毁。附加元数据（创建时间等）供列表展示。

    agent 由 Session 内部按名 Agent.load；本类只注入会话级零件
    （tool_config / 扩展声明池 / provider / 存储），create() 构造并登记。
    """

    def __init__(
        self,
        tool_config: ToolConfig | None = None,
        extensions: list[Extension] | None = None,
        provider: OpenAIProvider | None = None,
        storage: str = "sqlite",
        db_path: str = "data/session.db",
    ) -> None:
        self._tool_config = tool_config
        self._extensions = extensions or []  # 进程级扩展声明池
        self._provider = provider
        self._storage = storage
        self._db_path = db_path
        self._sessions: dict[str, Session] = {}

    def create(
        self,
        conn: Any,
        ext_names: list[str] | None = None,
    ) -> Session:
        """新建会话并注册。

        ext_names=None → 激活扩展池全部可用声明；给定名单 → 只激活命中的
        （跳过加载失败的声明）。
        """
        extensions = self.active_ext(ext_names)
        session = Session(
            session_id=conn.chat_id,
            tool_config=self._tool_config,
            extensions=extensions,
            provider=self._provider,
            storage=self._storage,
            db_path=self._db_path,
        )
        session.attach(conn)
        self.register(session)
        return session

    def active_ext(self, ext_names: list[str] | None) -> list[Extension]:
        """从扩展声明池按名单挑扩展（默认全部可用声明；跳过加载失败的）。"""
        pool = [e for e in self._extensions if e.error is None]
        if ext_names is None:
            return pool
        wanted = set(ext_names)
        return [e for e in pool if e.name in wanted]

    def register(self, session: Session) -> None:
        """注册新会话。同 id 已存在则报错（重连走 attach，不重建）。"""
        if session.id in self._sessions:
            raise ValueError(f"session '{session.id}' already registered")
        self._sessions[session.id] = session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> Session | None:
        """从注册表移除（不 close，调用方决定是否销毁）。"""
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

    async def close(self) -> None:
        """销毁全部会话（服务端停机）。"""
        for s in self._sessions.values():
            try:
                await s.close()
            except Exception:
                pass
        self._sessions.clear()
