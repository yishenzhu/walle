from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp.tools import Tool as MCPTool
from pydantic import BaseModel

from ..channel import Channel
from ..infra import PyKernel


@dataclass
class ToolContext:
    channel: Channel | None = None
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
