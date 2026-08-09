from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol

from mcp.server.fastmcp.tools import Tool as MCPTool
from pydantic import BaseModel

from ..channel import Channel
from ..infra import PyKernel
from ..schemas import Inquiry


class UserInteractor(Protocol):
    """工具与用户交互的最小接口：只发起 Inquiry。"""

    async def ask(self, question: str, options: list[str] | None = None) -> str: ...


class ChannelInteractor:
    """内部实现：把 ask 转成 channel.call(Inquiry(...))。"""

    def __init__(self, channel: Channel):
        self._channel = channel

    async def ask(self, question: str, options: list[str] | None = None) -> str:
        return await self._channel.call(Inquiry(question=question, options=options))


@dataclass
class ToolContext:
    interact: UserInteractor | None = None   # 最小权限：仅 Inquiry
    # 会话级计算资源：python 工具的持久解释器（按会话隔离，由 Runner 管理）
    kernel: PyKernel | None = None


tool_context: ContextVar[ToolContext | None] = ContextVar("tool_context", default=None)


class Tool(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[[dict[str, Any]], Awaitable[Any]]

    model_config = {"arbitrary_types_allowed": True}

    async def run(self, args: dict[str, Any]) -> Any:
        return await self.fn(args)

    def formatted_schema(self, strict: bool = True) -> dict[str, Any]:
        args = self.parameters.copy()
        if strict:
            args["additionalProperties"] = False
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "strict": strict,
                "parameters": args,
            },
        }

    @classmethod
    def from_function(
        cls,
        fn: Callable,
        name: str | None = None,
        description: str | None = None,
    ):
        tool = MCPTool.from_function(fn, name=name, description=description)
        return cls(
            name=name or tool.name,
            description=description or tool.description,
            parameters=tool.parameters,
            fn=tool.run,
        )
