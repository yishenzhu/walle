"""会话实体：每连接（session_id）一个 Session（连接即会话）。

Session 持有会话状态（历史 / kernel / agent），传输（CLIConn）即会话的
Channel 端点——chat_id 补全由连接负责（连接即会话，身份内聚在连接）。
连接断开即会话结束。
"""
from typing import Callable

from .agent import Agent
from .runner import Runner, RunOptions, SessionEnv
from ..channel import Channel
from ..infra import OpenAIProvider, PyKernel
from ..messages import InMemoryMessages
from ..schemas import UserInput


class Session:
    """单会话实体：会话状态 + 驱动 Runner，传输即其 Channel 端点。

    transport 由 CLIChannel 握手时注入（本连接的 CLIConn），作为会话的
    channel 端点直接使用（chat_id 补全在连接内完成）。
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
        # 执行环境打包：channel 直接用传输端点（连接即会话）
        self._env = SessionEnv(
            provider=self._provider,
            channel=self._transport,
            kernel=self._kernel,
            messages=self._messages,
        )

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
