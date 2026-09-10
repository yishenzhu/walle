"""核心数据类型：工具（Tool）与工具执行上下文注入点（tool_context）。

Job / SessionView 等跨层可见的类型已上收至 spec（schemas/protocol）；
本模块只保留 executor 与工具共同使用的具体实现（Tool）与 ContextVar。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp.tools import Tool as MCPTool

from ..spec import SessionView

tool_context: ContextVar[SessionView | None] = ContextVar("tool_context", default=None)


@dataclass
class Tool:
    """一个可被模型调用的工具：名称/描述/参数 schema/执行函数。

    纯数据 + 行为，不需要 pydantic 序列化（schema 由 formatted_schema 生成）。
    """

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[[dict[str, Any]], Awaitable[Any]]

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
