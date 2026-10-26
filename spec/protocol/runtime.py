"""会话工具表能力：工具 + 技能清单。"""

from typing import Any, Protocol, runtime_checkable

from .tool import ToolService


@runtime_checkable
class ToolTable(ToolService, Protocol):
    """会话工具表：工具注册/移除（插件与 define_tool）+ 技能清单。"""

    @property
    def skills(self) -> dict[str, Any]:
        """当前会话可用技能（name→Skill），供拼技能清单提示词。"""
