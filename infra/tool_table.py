"""会话工具表：工具与技能注册，Runner 每轮实时取源。"""

from __future__ import annotations

from collections.abc import Callable

from ..spec import Skill
from .tool import Tool


class SessionToolTable:
    """会话级工具/技能表：同名后到者覆盖先到者。"""

    def __init__(self) -> None:
        self._tools: list[Tool] = []
        self._skills: dict[str, Skill] = {}

    # ── 工具表（ToolService） ──────────────────────────
    def add(self, *tools: Tool) -> None:
        """注册工具：同名后到者覆盖先到者。同批重名视为编程错误。"""
        names = [t.name for t in tools]
        if len(set(names)) != len(names):
            raise ValueError(f"Duplicate tool name in batch: {names}")
        fresh = {t.name: t for t in tools}
        self._tools = [t for t in self._tools if t.name not in fresh]
        self._tools.extend(fresh.values())

    def remove(self, name: str) -> None:
        """按名摘除工具（插件卸载时用）。不存在则忽略。"""
        self._tools = [t for t in self._tools if t.name != name]

    def has(self, name: str) -> bool:
        return any(t.name == name for t in self._tools)

    def all_tools(self) -> list[Tool]:
        """本会话全部工具（Agent 工具源）。"""
        return list(self._tools)

    # ── 技能表 ────────────────────────────────────────
    def register_skill(
        self, name: str, description: str, path: str
    ) -> Callable[[], None]:
        """注册一个技能声明；返回注销函数。"""
        self._skills[name] = Skill(name=name, description=description, path=path)

        def _off() -> None:
            self._skills.pop(name, None)

        return _off

    def remove_skill(self, name: str) -> None:
        self._skills.pop(name, None)

    @property
    def skills(self) -> dict[str, Skill]:
        """本会话可用技能（name→Skill），供 system prompt 拼技能清单。"""
        return dict(self._skills)
