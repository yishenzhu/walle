"""插件激活能力：会话工具表 + 动态指令。"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ToolTable(Protocol):
    """会话工具表：注册 / 移除（插件与 define_tool）+ 读取（runner 取工具源）。"""

    def register_tool(self, *tools) -> None: ...

    def remove_tool(self, name: str) -> None: ...

    def all_tools(self) -> list[Any]:
        """当前会话全部可用工具（每轮实时取）。"""

    @property
    def skills(self) -> dict[str, Any]:
        """当前会话可用技能（name→Skill），供拼技能清单提示词。"""


@runtime_checkable
class PluginLayer(ToolTable, Protocol):
    """会话插件激活层：工具表 + 动态指令（每次模型调用前取）。"""

    def instructions(self, metadata: dict[str, Any]) -> list[str]:
        """按会话 metadata 返回插件追加的系统指令（空结果跳过）。"""
