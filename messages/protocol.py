from collections.abc import Sequence
from typing import Protocol, runtime_checkable
from ..schemas import Message, Usage


@runtime_checkable
class Messages(Protocol):
    """对话记录存储协议：按时间追加消息，可读回会话上下文。

    Runner 用它存取会话的对话历史；实现可替换（内存 / SQLite / 压缩）。
    """

    async def get(self, limit: int | None = None) -> list[Message]: ...

    async def add(self, items: Sequence[Message], usage: Usage | None = None): ...

    async def clear(self): ...

    async def pop(self) -> Message | None: ...

    async def close(self): ...
