from collections.abc import Sequence
from ..schemas import Message, Usage


class InMemorySession:
    def __init__(self):
        self._items: list[Message] = []

    async def get(self, limit: int | None = None) -> list[Message]:
        if limit is not None:
            return self._items[-limit:]
        return list(self._items)

    async def add(self, items: Sequence[Message], usage: Usage | None = None):
        if not items:
            return
        self._items.extend(items)

    async def pop(self) -> Message | None:
        if not self._items:
            return None
        return self._items.pop(0)

    async def clear(self):
        self._items.clear()

    async def close(self):
        pass
