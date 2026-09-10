"""扩展激活与工具注册能力：工具执行期动态注册面。

只声明工具表能力（注册 / 移除 / 读取），具体由会话侧扩展激活层实现。
放在协议包最底层，避免 infra 内部相互引用成环。
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ToolTable(Protocol):
    """会话工具表：注册 / 移除（扩展与 define_tool）+ 读取（runner 取工具源）。

    同一对象由扩展激活层（infra.ExtensionRunner）实现；工具经 SessionView
    拿到本面动态注册工具，Runner 经本面取每轮工具源与技能清单。
    """

    def register_tool(self, *tools) -> None: ...

    def remove_tool(self, name: str) -> None: ...

    def all_tools(self) -> list[Any]:
        """当前会话全部可用工具（每轮实时取）。"""

    @property
    def skills(self) -> dict[str, Any]:
        """当前会话可用技能（name→Skill），供拼技能清单提示词。"""
