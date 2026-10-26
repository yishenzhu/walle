"""工具服务能力面（对齐 deepseek-harness 的 `ctx.tools`）。"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ToolService(Protocol):
    """会话工具表：插件注册/移除工具，Runner 每轮实时取工具源。"""

    def add(self, *tools) -> None: ...

    def remove(self, name: str) -> None: ...

    def has(self, name: str) -> bool:
        """该名字是否有已注册工具（不涉及作用域限制）。"""

    def all_tools(self) -> list[Any]:
        """当前可见的工具对象（供 Runner 组装每轮工具表）。"""
