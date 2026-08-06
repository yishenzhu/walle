"""Obsidian 笔记检索器。

基于 Store 的 FTS5 索引做 BM25 相关性检索。
返回带来源（path + heading_path）的块，供 LLM 溯源。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3

from pydantic import BaseModel

from .indexer import Indexer
from .store import Store
from .tokenize import tokenize

logger = logging.getLogger(__name__)

_FTS_SPECIAL = '"*'  # FTS5 MATCH 语法中的特殊字符，需转义


class Retrieval(BaseModel):
    path: str            # vault 内相对路径
    heading_path: str    # 祖先链 + 叶子标题，如 "生产库 / 连接方式"
    content: str         # 块内容
    score: float         # bm25 得分（SQLite 返回，越小越相关）


class Retriever:
    def __init__(self, store: Store, indexer: Indexer | None = None):
        self._store = store
        self._indexer = indexer

    async def retrieve(self, query: str, k: int = 3) -> list[Retrieval]:
        # 懒刷新：检索前同步索引（mtime 增量），捕捉任何来源的新写入
        if self._indexer is not None:
            await self._indexer.refresh()
        match = _escape_match(query)
        if not match:
            return []
        conn = await self._store._get_conn()

        def _run():
            rows = conn.execute(
                """
                SELECT c.path, c.heading, c.ancestors, c.content,
                       bm25(chunks_fts) AS score
                FROM chunks_fts
                JOIN chunks c ON c.id = chunks_fts.rowid
                WHERE chunks_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (match, k * 2),  # 多取一些，去重后仍够 k 条
            ).fetchall()
            return rows

        rows = await asyncio.to_thread(_run)
        seen: set[tuple[str, str]] = set()
        results: list[Retrieval] = []
        for path, heading, ancestors_json, content, score in rows:
            ancestors = json.loads(ancestors_json)
            heading_path = " / ".join([*ancestors, heading]) if heading else " / ".join(ancestors)
            key = (path, heading_path)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                Retrieval(
                    path=path,
                    heading_path=heading_path,
                    content=content,
                    score=score,
                )
            )
            if len(results) >= k:
                break
        return results


def _escape_match(query: str) -> str:
    """把用户查询分词后转成安全的 FTS5 MATCH 表达式（OR 组合，宽召回）。"""
    q = query.strip()
    if not q:
        return ""
    terms = [t for t in tokenize(q).split() if t]
    if not terms:
        return ""
    parts = []
    for t in terms:
        for ch in _FTS_SPECIAL:
            t = t.replace(ch, " ")
        t = t.strip()
        if t:
            parts.append(f'"{t}"')
    return " OR ".join(parts)
