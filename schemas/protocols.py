"""跨层共享的结构化协议：只定义能力（interface），具体实现在各自模块。

放最底层 schemas，避免 infra/messages 相互 import 成环。统一从
schemas 顶层导出（schemas/__init__），使用方不 import 本子模块。
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .message import Message
from .usage import Usage


@runtime_checkable
class Messages(Protocol):
    """消息存储：按时间追加、可读回会话历史（内存/SQLite/投影实现）。"""

    async def get(self, limit: int | None = None) -> list[Message]: ...

    async def add(self, items: Sequence[Message], usage: Usage | None = None): ...

    async def clear(self): ...

    async def pop(self) -> Message | None: ...

    async def close(self): ...

    async def query(self, offset: int = 0, limit: int = 20) -> list[Message]:
        """窗口查询：按追加顺序取 [offset, offset+limit) 的底层原文。"""

    async def search(self, query: str = "", limit: int = 20) -> list[Message]:
        """关键词检索：匹配 query 的最近 limit 条原文（倒序），空 = 最近。"""

    async def count(self) -> int:
        """底层原文总条数。"""


@runtime_checkable
class Projection(Protocol):
    """可投影消息存储：底层原文全量保留，读取时按切点截断模型视野。"""

    @property
    def underlying(self) -> Messages:
        """被包装的底层存储（读原文 / 窗口查询）。"""

    async def set_projection(self, cut: int) -> None:
        """设切点：前 cut 条移出模型视野（原文保留）。"""

    async def clear_projection(self) -> None:
        """清除切点，恢复底层原始视图。"""

    async def projection(self) -> int | None:
        """读当前切点；无投影返回 None。"""


@runtime_checkable
class ProjectionStore(Protocol):
    """切点持久化：落盘后重启恢复投影（避免折叠失效）。"""

    async def load(self) -> int | None: ...

    async def save(self, cut: int) -> None: ...

    async def clear(self) -> None: ...


@runtime_checkable
class ExtRunner(Protocol):
    """扩展激活层（ExtensionRunner 的能力面，工具执行期经 ctx.ext 使用）。

    协议只暴露工具表管理，供 define_tool 等动态注册；ExtensionRunner 是
    实现。放最底层避免 infra/tool 与 infra/extension 相互 import 成环。
    """

    def register_tool(self, *tools) -> None: ...

    def remove_tool(self, name: str) -> None: ...
