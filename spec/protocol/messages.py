"""消息存储能力：历史读写 / 投影 / 切点持久化。

Runner 只依赖本协议面做读写，具体存储形态（内存/持久化/投影包装）
由使用侧在装配时注入。
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..schemas import Message, Usage


@runtime_checkable
class Messages(Protocol):
    """消息存储：按时间追加、可读回会话历史。"""

    async def get(self, limit: int | None = None) -> list[Message]: ...

    async def add(self, items: Sequence[Message], usage: Usage | None = None): ...

    async def clear(self): ...

    async def pop(self) -> Message | None: ...

    async def close(self): ...

    async def query(self, offset: int = 0, limit: int = 20) -> list[Message]:
        """窗口查询：按追加顺序取 [offset, offset+limit) 的原始消息。"""

    async def search(self, query: str = "", limit: int = 20) -> list[tuple[int, Message]]:
        """关键词检索：词间 AND 子串匹配，返回 (绝对序号, 消息) 倒序最近 limit 条。

        debug: 绝对序号 = 消息在会话中的位置（同 query 的 offset 寻址），命中后
        可直接用该序号查看附近窗口。
        """

    async def count(self) -> int:
        """原始消息总条数。"""


@runtime_checkable
class Projection(Protocol):
    """可投影消息存储：原始消息全量保留，读取时按切点截断模型视野。"""

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