"""扩展激活与工具注册能力：工具执行期动态注册面。

只声明工具表管理能力（register_tool / remove_tool），具体由会话侧
扩展激活层实现。放在协议包最底层，避免 infra 内部相互引用成环。
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class ToolTable(Protocol):
    """工具表管理能力：注册 / 移除工具（激活层实现，工具源与 define_tool 依赖此面）。"""

    def register_tool(self, *tools) -> None: ...

    def remove_tool(self, name: str) -> None: ...