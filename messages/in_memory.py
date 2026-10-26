from collections.abc import Sequence

from ..spec import Message
from .search import keyword_hits


class InMemoryMessages:
    def __init__(self):
        self._items: list[Message] = []

    async def get(self) -> list[Message]:
        return list(self._items)

    async def query(self, offset: int = 0, limit: int = 20) -> list[Message]:
        """窗口查询：按追加顺序取 [offset, offset+limit) 的原文。"""
        if offset < 0 or limit < 0:
            raise ValueError("offset/limit must be >= 0")
        return list(self._items[offset : offset + limit])

    async def search(self, query: str = "", limit: int = 20) -> list[tuple[int, Message]]:
        """关键词检索：词间 AND 子串匹配（大小写不敏感），倒序返回最近 limit 条。"""
        return keyword_hits(self._items, query, limit)

    async def count(self) -> int:
        return len(self._items)

    async def add(self, items: Sequence[Message]):
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
