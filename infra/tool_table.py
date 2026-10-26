"""会话工具表（ToolService）：工具注册与查询，Runner 每轮实时取源。"""

from __future__ import annotations

from ..plugin.runtime import current_scope
from .tool import Tool


class ToolTable:
    """会话级工具表：同名后到者覆盖先到者。"""

    def __init__(self) -> None:
        self._tools: list[Tool] = []

    # ── 工具表（ToolService） ──────────────────────────
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
