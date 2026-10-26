"""工具：值类型（Tool）+ 会话工具表（Tools）+ 执行期上下文注入点。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp.tools import Tool as MCPTool

from ..plugin.runtime import current_scope
from ..spec import SessionView

tool_context: ContextVar[SessionView | None] = ContextVar("tool_context", default=None)


@dataclass
class Tool:
    """一个可被模型调用的工具：名称/描述/参数 schema/执行函数。"""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[[dict[str, Any]], Awaitable[Any]]

    async def run(self, args: dict[str, Any]) -> Any:
        return await self.fn(args)

    def to_schema(self, strict: bool = True) -> dict[str, Any]:
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


class Tools:
    """会话级工具表（ToolService）：同名后到者覆盖先到者。"""

    def __init__(self) -> None:
        self._tools: list[Tool] = []

    def add(self, *tools: Tool) -> None:
        """注册工具：同名后到者覆盖先到者。同批重名视为编程错误。"""
        names = [t.name for t in tools]
        if len(set(names)) != len(names):
            raise ValueError(f"Duplicate tool name in batch: {names}")
        fresh = {t.name: t for t in tools}
        self._tools = [t for t in self._tools if t.name not in fresh]
        self._tools.extend(fresh.values())
        scope = current_scope.get()
        if scope is not None:  # 插件 apply 期间注册：随插件卸载回收
            for tool in tools:
                scope.effect(lambda tool=tool: self.discard(tool))

    def remove(self, name: str) -> None:
        """按名摘除工具。不存在则忽略。"""
        self._tools = [t for t in self._tools if t.name != name]

    def discard(self, tool: Tool) -> None:
        """按实例摘除（插件卸载回收）；同名已被后来者覆盖时不动。"""
        self._tools = [t for t in self._tools if t is not tool]

    def has(self, name: str) -> bool:
        return any(t.name == name for t in self._tools)

    def all_tools(self) -> list[Tool]:
        """本会话全部工具（Agent 工具源）。"""
        return list(self._tools)
