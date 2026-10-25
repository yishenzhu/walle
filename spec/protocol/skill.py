"""技能服务能力面：技能目录（name → 描述/路径）。"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillService(Protocol):
    """会话技能目录：注册/移除/列表（含 glob 筛选）。"""

    def add(self, name: str, description: str, path: str) -> None: ...

    def remove(self, name: str) -> None: ...

    def list(
        self,
        *,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> list[Any]:
        """技能列表（name→描述/路径；include None=全部，[]=空集，exclude 优先）。"""
