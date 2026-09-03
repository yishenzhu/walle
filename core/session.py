"""会话实体：对齐 pi 的 AgentSession——每个会话自持一套运行时。

会话 = 独立边界：自己的事件总线(bus)、工具表(tools)、审批策略
(executor)、扩展激活(ExtensionRunner)、agent 循环(Runner)。扩展声明
(ExtensionRegistry 加载的 Extension)跨会话共享，但"激活到哪个会话"
由本会话的 ExtensionRunner 决定——不同会话可激活不同扩展组合。

生命周期：transport 可切换（attach/detach）。连接断开时 detach 保留
messages（状态跨连接存活，供重连恢复）；真正销毁走 close()。
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .agent import Agent
from .extensions import Extension, ExtensionRegistry, ExtensionRunner
from .runner import Runner, RunOptions, SessionEnv
from ..channel import Channel
from ..conf import ToolConfig
from ..infra import EventBus, OpenAIProvider
from ..messages import Messages, InMemoryMessages, SQLiteMessages
from ..schemas import Delta, DeltaEnd, UserInput
from ..tools import Job, MCPRegistry, ToolRegistry
from .executor import ToolExecutor


class Session:
    """单会话运行时：自持 bus / 工具表 / 审批 / 扩展激活 / agent 循环。

    transport 由 attach() 注入（通常为某连接的 CLIConn），作为会话的
    channel 端点直接使用（chat_id 补全在连接内完成）。mcp 为进程级共享
    的 MCP 客户端容器（各会话工具表共享其远端工具视图）。
    """

    def __init__(
        self,
        session_id: str,
        agent_factory: Callable[[str], Agent],
        tool_config: ToolConfig | None = None,
        extensions: list[Extension] | None = None,
        mcp: MCPRegistry | None = None,
        transport: Channel | None = None,
        provider: OpenAIProvider | None = None,
        storage: str = "sqlite",
        db_path: str = "data/session.db",
        created_at: float | None = None,
    ) -> None:
        self.id = session_id
        # 创建时间内聚在 Session（注册表/连接仅读取展示）
        self.created_at = created_at if created_at is not None else time.time()

        # ── 会话级运行时（每会话独立，对齐 pi AgentSession）──
        self._bus = EventBus()  # 会话私有事件总线（扩展事件/工具钩子按会话隔离）
        self._tools = ToolRegistry(mcp)  # 会话工具表（共享进程级 MCP 视图）
        self._tool_executor = ToolExecutor(tool_config or ToolConfig())  # 会话私有审批策略
        self._agent_runner = Runner(executor=self._tool_executor, bus=self._bus)
        self._ext_runner = ExtensionRunner(self._bus, self._tools)  # 会话级扩展激活
        if extensions:
            self._ext_runner.activate(*extensions)  # 按会话选择激活扩展

        # agent_factory 接收 agent 名（缺省 = default），工具源由会话覆写为会话工具表
        self._agent_builder = agent_factory
        self._agent = self._build_agent()
        self._provider = provider
        # 会话状态：历史（每会话隔离）
        if storage == "memory":
            self._messages: Messages = InMemoryMessages()
        else:
            self._messages = SQLiteMessages(db_path=db_path, session_id=session_id)
        # 后台作业表：跨轮存活（background 写入 pending，executor 拉起，job_result 读取）
        self._jobs: dict[str, Job] = {}
        # 执行环境打包：channel 随 attach/detach 切换
        self._transport: Channel | None = transport
        self._env = SessionEnv(
            provider=self._provider,
            channel=self._transport,
            messages=self._messages,
            session_id=self.id,
            jobs=self._jobs,
        )

    def _build_agent(self, name: str | None = None) -> Agent:
        """按名构造 agent，并把工具源绑定到本会话的工具表。"""
        agent = self._agent_builder(name)
        agent.tools = self._tools.all_tools  # 本会话工具表
        return agent

    @property
    def jobs(self) -> dict[str, Job]:
        """后台作业表（供 registry 停机时统一取消）。"""
        return self._jobs

    @property
    def tools(self) -> ToolRegistry:
        """本会话的工具表（扩展激活后内置 + 选中扩展的工具在此）。"""
        return self._tools

    @property
    def tool_executor(self) -> ToolExecutor:
        """本会话的工具执行器（审批策略随会话）。"""
        return self._tool_executor

    @property
    def agent_runner(self) -> Runner:
        """本会话的 agent 循环执行器（持本会话 bus）。"""
        return self._agent_runner

    @property
    def attached(self) -> bool:
        """是否有活跃连接的 transport 绑定。"""
        return self._transport is not None

    @property
    def ext_runner(self) -> ExtensionRunner:
        """本会话的扩展激活层（命令分发/动态激活扩展）。"""
        return self._ext_runner

    def set_agent(self, name: str = "default") -> None:
        """按名切换 agent（builder 重新加载 frontmatter）；缺省用 default。

        历史保留，仅换 agent 配置——同一会话可随时切换。
        """
        self._agent = self._build_agent(name)

    def attach(self, transport: Channel) -> None:
        """绑定新 transport（重连/接管）：换 channel 端点，环境随之更新。"""
        self._transport = transport
        self._env.channel = transport

    def detach(self) -> None:
        """解除 transport：保留 messages 状态，会话仍可被 attach 恢复。"""
        self._transport = None
        self._env.channel = None

    async def handle(self, user_input: UserInput) -> None:
        if self._transport is None:
            raise RuntimeError(f"session '{self.id}' is detached, attach first")
        content = user_input.content or ""
        # 斜杠命令：命中则不经 agent，直接回复；未命中回退给 agent
        reply = await self._ext_runner.dispatch(content)
        if reply is not None:
            await self._transport.notify(Delta(delta=reply))
            await self._transport.notify(DeltaEnd())
            return
        await self._agent_runner.run(
            self._agent,
            content,
            env=self._env,
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

    会话运行时零件（agent_builder / tool_config / 扩展声明池）由组装层
    注入，create() 据此构造每会话自持的运行时——注册表只负责创建登记。
    """

    def __init__(
        self,
        agent_factory: Callable[[str], Agent],
        tool_config: ToolConfig | None = None,
        extensions: list[Extension] | None = None,
        mcp: MCPRegistry | None = None,
        provider: OpenAIProvider | None = None,
        storage: str = "sqlite",
        db_path: str = "data/session.db",
    ) -> None:
        self._agent_factory = agent_factory
        self._tool_config = tool_config
        self._extensions = extensions or []  # 进程级扩展声明池
        self._mcp = mcp  # 进程级共享 MCP 客户端容器
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
        extensions = self._select_extensions(ext_names)
        session = Session(
            session_id=conn.chat_id,
            agent_factory=self._agent_factory,
            tool_config=self._tool_config,
            extensions=extensions,
            mcp=self._mcp,
            provider=self._provider,
            storage=self._storage,
            db_path=self._db_path,
        )
        session.attach(conn)
        self.register(session)
        return session

    def _select_extensions(self, ext_names: list[str] | None) -> list[Extension]:
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
