"""工具服务能力面（对齐 deepseek-harness 的 `ctx.tools`）。"""

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from ..schemas.tools import ToolRestriction


@runtime_checkable
class ToolService(Protocol):
    def add(self, *tools) -> None: ...

    def remove(self, name: str) -> None: ...

    def has(self, name: str) -> bool:
        """该名字是否有已注册工具（不涉及作用域限制）。"""

    def all_tools(self) -> list[Any]:
        """当前可见的工具对象（供 Runner 组装每轮工具表）。"""

    def restrict(self, restriction: ToolRestriction) -> Callable[[], None]:
        """对本作用域继承来的工具追加一层限制（多层取交集）；返回撤销函数。"""

    def schemas(self, *, restriction: ToolRestriction | None = None) -> list[dict[str, Any]]:
        """工具 schema 列表（喂 LLM），按作用域限制投影。"""

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        tool_call_id: str = "",
        *,
        restriction: ToolRestriction | None = None,
        tools: dict[str, Any] | None = None,
    ) -> Any:
        """执行单个工具调用，返回结果或错误字符串（工具级失败不抛）。"""

    async def launch_pending(self) -> None:
        """把 background 写下的 pending 作业拉起（本轮工具执行完后调用）。"""
