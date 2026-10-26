"""插件运行时适配层：把 Context/Service 能力桥接到现有会话运行时。"""

from __future__ import annotations

from fnmatch import fnmatch

from ..infra import SessionToolTable, Skill


class SessionSkillService:
    """SkillService 适配：注册落到会话工具表的技能表。"""

    def __init__(self, table: SessionToolTable) -> None:
        self._table = table

    def add(self, name: str, description: str, path: str) -> None:
        self._table.register_skill(name, description, path)

    def remove(self, name: str) -> None:
        self._table.remove_skill(name)

    def list(
        self,
        *,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> list[Skill]:
        return [
            skill
            for name, skill in self._table.skills.items()
            if _matches(name, include, exclude)
        ]


class SessionAgentService:
    """AgentService 适配：当前 agent 与按名加载。"""

    def __init__(self, session) -> None:
        self._session = session

    @property
    def current(self):
        return self._session.agent

    def get(self, name: str):
        return self._session.get_agent(name)


def _matches(name: str, include: list[str] | None, exclude: list[str] | None) -> bool:
    """glob 筛选：include 白名单（None=全收，[]=全拒）；exclude 优先。"""
    if exclude and any(fnmatch(name, p) for p in exclude):
        return False
    if include is None:
        return True
    return any(fnmatch(name, p) for p in include)
