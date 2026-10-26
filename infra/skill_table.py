"""会话技能表（SkillService）：技能声明的注册、摘除与列表。"""

from __future__ import annotations

from fnmatch import fnmatch

from ..plugin.runtime import current_scope
from ..spec import Skill


class SkillTable:
    """会话级技能表：同名后到者覆盖先到者。"""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def add(self, name: str, description: str, path: str) -> None:
        """注册技能；插件 apply 期间注册的随插件卸载回收。"""
        skill = Skill(name=name, description=description, path=path)
        self._skills[name] = skill

        def _off() -> None:
            if self._skills.get(name) is skill:  # 同名被覆盖时不误删
                del self._skills[name]

        scope = current_scope.get()
        if scope is not None:
            scope.effect(_off)

    def remove(self, name: str) -> None:
        """按名摘除技能。不存在则忽略。"""
        self._skills.pop(name, None)

    def list(
        self,
        *,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> list[Skill]:
        """技能列表（glob 筛选：include None=全部，[]=空集，exclude 优先）。"""
        return [
            skill
            for name, skill in self._skills.items()
            if _matches(name, include, exclude)
        ]


def _matches(name: str, include: list[str] | None, exclude: list[str] | None) -> bool:
    """glob 筛选：include 白名单（None=全收，[]=全拒）；exclude 优先。"""
    if exclude and any(fnmatch(name, p) for p in exclude):
        return False
    if include is None:
        return True
    return any(fnmatch(name, p) for p in include)
