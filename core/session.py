"""会话实体：每连接（session_id）一个 Session（连接即会话）。

Session 持有会话状态（历史 / kernel / agent），传输（CLIConn）即会话的
Channel 端点——chat_id 补全由连接负责（连接即会话，身份内聚在连接）。

生命周期：transport 可切换（attach/detach）。连接断开时 detach 保留
kernel/messages（状态跨连接存活，供重连恢复）；连接接入时 attach 换新
transport。真正销毁走 close()（registry 显式调用）。
"""
from typing import Any, Callable

from .agent import Agent
from .runner import Runner, RunOptions, SessionEnv
from ..channel import Channel
from ..infra import OpenAIProvider, PyKernel
from ..messages import Messages, InMemoryMessages, SQLiteMessages
from ..schemas import UserInput
class Session:
    """单会话实体：会话状态 + 驱动 Runner，transport 是其 Channel 端点。

    transport 由 attach() 注入（通常为某连接的 CLIConn），作为会话的
    channel 端点直接使用（chat_id 补全在连接内完成）。
    """

    def __init__(
        self,
        session_id: str,
        agent_factory: Callable[[str], Agent],
        runner: Runner,
        transport: Channel | None = None,
        provider: OpenAIProvider | None = None,
        storage: str = "sqlite",
        db_path: str = "data/session.db",
    ) -> None:
        self.id = session_id
        # factory 接收 agent 名（缺省 = default）：会话内可随时按名切换 agent
        self._agent_factory = agent_factory
        self._agent = agent_factory()
        self._runner = runner
        self._provider = provider
        # 会话状态：历史 + kernel（每会话隔离）
        # 历史持久化：默认 SQLite（跨连接/重启保留），可配置切回内存
        if storage == "memory":
            self._messages: Messages = InMemoryMessages()
        else:
            self._messages = SQLiteMessages(db_path=db_path, session_id=session_id)
        self._kernel = PyKernel()
        # 执行环境打包：channel 随 attach/detach 切换
        self._transport: Channel | None = transport
        self._env = SessionEnv(
            provider=self._provider,
            channel=self._transport,
            kernel=self._kernel,
            messages=self._messages,
        )

    @property
    def attached(self) -> bool:
        """是否有活跃连接的 transport 绑定。"""
        return self._transport is not None

    def set_agent(self, name: str = "default") -> None:
        """按名切换 agent（factory 重新加载 frontmatter）；缺省用 default。

        历史/kernel 保留，仅换 agent 配置——同一会话可随时切换。
        """
        self._agent = self._agent_factory(name)

    def attach(self, transport: Channel) -> None:
        """绑定新 transport（重连/接管）：换 channel 端点，环境随之更新。"""
        self._transport = transport
        self._env.channel = transport

    def detach(self) -> None:
        """解除 transport：保留 kernel/messages 状态，会话仍可被 attach 恢复。"""
        self._transport = None
        self._env.channel = None

    async def handle(self, user_input: UserInput) -> None:
        if self._transport is None:
            raise RuntimeError(f"session '{self.id}' is detached, attach first")
        await self._runner.run(
            self._agent,
            user_input.content,
            env=self._env,
            options=RunOptions(streamed=True),
        )

    async def close(self) -> None:
        """真正销毁：关 kernel + 消息存储。"""
        await self._kernel.close()
        await self._messages.close()


class SessionRegistry:
    """进程级会话注册表 + 工厂：session_id → Session。

    连接断开只 detach（保留状态），会话仍在 registry 中可被重连 attach；
    显式 remove/close 才真正销毁。附加元数据（创建时间等）供列表展示。

    Session 构造参数（agent_factory / runner / 存储配置）由组装层直接注入，
    create() 据此构造——注册表是会话的持有者，负责创建与登记。
    """

    def __init__(
        self,
        agent_factory: Callable[[str], Agent],
        runner: Runner,
        provider: OpenAIProvider | None = None,
        storage: str = "sqlite",
        db_path: str = "data/session.db",
    ) -> None:
        self._agent_factory = agent_factory
        self._runner = runner
        self._provider = provider
        self._storage = storage
        self._db_path = db_path
        self._sessions: dict[str, Session] = {}
        self._created_at: dict[str, float] = {}

    def create(self, conn: Any) -> Session:
        """用注入的构造参数新建会话，绑定 conn 为 transport 并注册。

        Session 初始用默认 agent（factory 内部业务），运行时可在会话内
        按名切换（Session.set_agent）。
        """
        session = Session(
            session_id=conn.chat_id,
            agent_factory=self._agent_factory,
            runner=self._runner,
            provider=self._provider,
            storage=self._storage,
            db_path=self._db_path,
        )
        session.attach(conn)
        self.register(session)
        return session

    def register(self, session: Session) -> None:
        """注册新会话。同 id 已存在则报错（重连走 attach，不重建）。"""
        if session.id in self._sessions:
            raise ValueError(f"session '{session.id}' already registered")
        import time

        self._sessions[session.id] = session
        self._created_at[session.id] = time.time()

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> Session | None:
        """从注册表移除（不 close，调用方决定是否销毁）。"""
        self._created_at.pop(session_id, None)
        return self._sessions.pop(session_id, None)

    def list(self) -> list[dict]:
        """列出全部会话：id、attached 状态、创建时间。"""
        import time as _time

        return [
            {
                "session_id": sid,
                "attached": s.attached,
                "created_at": self._created_at.get(sid, 0.0),
                "age_seconds": _time.time() - self._created_at.get(sid, _time.time()),
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
        self._created_at.clear()
