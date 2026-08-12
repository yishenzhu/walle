"""会话实体：每连接（session_id）一个 Session（连接即会话）。

Session 自身是绑定 chat_id 的端点（notify/call 填 chat_id 后转发到底层
连接），并持有会话状态（历史 / kernel / agent）。连接断开即会话结束。
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
