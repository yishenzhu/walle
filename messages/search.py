"""关键词检索：file / in-memory 后端共用的子串匹配。"""

from collections.abc import Sequence

from ..spec import Message


def keyword_hits(
    items: Sequence[Message], query: str, limit: int
) -> list[tuple[int, Message]]:
    """词间 AND、大小写不敏感的子串匹配；返回 (绝对序号, 消息) 倒序最近 limit 条。"""
    words = [w for w in query.lower().split() if w]
    matched = [
        (i, m)
        for i, m in enumerate(items)
        if all(w in (m.content or "").lower() for w in words)
    ]
    matched.reverse()
    return matched[:limit]
