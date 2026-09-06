"""跨层共享的结构化协议（零依赖，供 infra / messages / core 引用）。

放在最底层 schemas，避免 infra/messages 相互 import 造成循环。
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .message import Message
from .usage import Usage


@runtime_checkable
class Messages(Protocol):
    """对话记录存储协议：按时间追加消息，可读回会话上下文。

    Runner 用它存取会话的对话历史；实现可替换（内存 / SQLite / 压缩 /
    投影）。放最底层以便 infra（事件载荷）与 messages 共同引用而无环。
    """

    async def get(self, limit: int | None = None) -> list[Message]: ...

    async def add(self, items: Sequence[Message], usage: Usage | None = None): ...

    async def clear(self): ...

    async def pop(self) -> Message | None: ...

    async def close(self): ...


@runtime_checkable
class Projection(Protocol):
    """可投影存储：在 Messages 之上提供「摘要 + 切点」视图。

    投影存储保留底层全量原文，读取时折叠切点前消息为摘要。压缩扩展只
    依赖本协议而非具体实现（ProjectedMessages）。
    """

    @property
    def underlying(self) -> Messages:
        """被包装的底层存储（读原文 / 窗口查询）。"""

    async def set_projection(self, cut: int, summary: str) -> None:
        """设切点与摘要：前 cut 条消息在投影中折叠为 summary。"""
