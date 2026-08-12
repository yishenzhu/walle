"""会话管理：Session 会话实体 + SessionRouter 注册表。

每连接（session_id）一个 Session。Session 自身是绑定 chat_id 的端点
（notify/call 填 chat_id 后转发到底层通道），并持有会话状态（历史 /
kernel / agent）。SessionRouter 按 session_id 取/建会话，作为消息入口
（dispatcher）供通道注入。
"""
from typing import Callable

from .agent import Agent
from .runner import Runner, RunOptions, SessionEnv
from ..channel import Channel
from ..infra import OpenAIProvider, PyKernel
from ..messages import InMemoryMessages
from ..schemas import UserInput


class Session:
    """单会话实体：绑定 session_id 的端点 + 会话状态，驱动 Runner。

    Session 本身实现 Channel 协议：notify/call 填 chat_id 后转发到底层
    传输（CLIChannel），由通道按 chat_id 路由到对应连接。
    """

    def __init__(
        self,
        session_id: str,
        transport: Channel,
        agent_factory: Callable[[], Agent],
        runner: Runner,
        provider: OpenAIProvider | None = None,
    ) -> None:
        self.id = session_id
        self._transport = transport
        self._agent = agent_factory()
        self._runner = runner
        self._provider = provider
        # 会话状态：历史 + kernel（每会话隔离）
        self._messages = InMemoryMessages()
        self._kernel = PyKernel()
        # 执行环境打包：channel 用自身（绑定本会话 chat_id）
        self._env = SessionEnv(
            provider=self._provider,
            channel=self,
            kernel=self._kernel,
            messages=self._messages,
        )

    # ── Channel 协议：填本会话 chat_id 后转发 ─────────
    async def notify(self, n) -> None:
        n = n.model_copy(update={"chat_id": self.id})
        await self._transport.notify(n)

    async def call(self, s):
        s = s.model_copy(update={"chat_id": self.id})
        return await self._transport.call(s)

    async def handle(self, user_input: UserInput) -> None:
        await self._runner.run(
            self._agent,
            user_input.content,
            env=self._env,
            options=RunOptions(streamed=True),
        )

    async def close(self) -> None:
        await self._kernel.close()
        await self._messages.close()


class SessionRouter:
    """会话注册表：按 session_id 取/建 Session（每会话独立 agent）。

    作为通道的 dispatcher：收到带 chat_id 的消息 → 取/建会话 → 处理一轮。
    """

    def __init__(
        self,
        transport: Channel,
        agent_factory: Callable[[], Agent],
        runner: Runner,
    ):
        self._transport = transport
        self._agent_factory = agent_factory
        self._runner = runner
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str) -> Session:
        s = self._sessions.get(session_id)
        if s is None:
            s = Session(
                session_id=session_id,
                transport=self._transport,
                agent_factory=self._agent_factory,
                runner=self._runner,
            )
            self._sessions[session_id] = s
        return s

    async def dispatch(self, user_input: UserInput) -> None:
        """按 session_id 取会话，交由 Session 处理一轮。"""
        s = self.get_or_create(user_input.chat_id)
        await s.handle(user_input)

    async def close(self) -> None:
        for s in self._sessions.values():
            await s.close()
        self._sessions.clear()
