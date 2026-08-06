"""Obsidian 笔记块索引存储。

管理两块表：
- chunks       块级主表（path / heading / ancestors / content / tags）
- chunks_fts   FTS5 全文索引（外部内容表，content='chunks'）

祖先链（ancestors）以 JSON 数组存储，天然支持不同深度的知识树。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Sequence

from ..infra.sqlite_store import SQLiteStore
from .parser import Chunk

DEFAULT_DB_PATH = "data/vault.db"


class Store(SQLiteStore):
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        super().__init__(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = super()._connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                heading TEXT NOT NULL,
                ancestors TEXT NOT NULL,
                content TEXT NOT NULL,
                search_text TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks (path)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                mtime REAL NOT NULL
            )
            """
        )
        self._ensure_fts(conn)
        conn.commit()
        return conn

    def _ensure_fts(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                search_text, path,
                content='chunks', content_rowid='id'
            )
            """
        )
        # FTS5 外部内容表不会自动同步 DML，需触发器桥接
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, search_text, path)
                VALUES (new.id, new.search_text, new.path);
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, search_text, path)
                VALUES ('delete', old.id, old.search_text, old.path);
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, search_text, path)
                VALUES ('delete', old.id, old.search_text, old.path);
                INSERT INTO chunks_fts(rowid, search_text, path)
                VALUES (new.id, new.search_text, new.path);
            END
            """
        )

    async def upsert_file(self, path: str, chunks: Sequence[Chunk]) -> None:
        """替换单个文件的所有块（先删旧块，再插入新块）。"""
        rows = [
            (
                c.path,
                c.heading,
                json.dumps(c.ancestors, ensure_ascii=False),
                c.content,
                c.search_text,
                json.dumps(c.tags, ensure_ascii=False),
            )
            for c in chunks
        ]
        await self.execute("DELETE FROM chunks WHERE path = ?", (path,))
        await self.executemany(
            """
            INSERT INTO chunks (path, heading, ancestors, content, search_text, tags)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    async def delete_file(self, path: str) -> None:
        await self.execute("DELETE FROM chunks WHERE path = ?", (path,))
        await self.execute("DELETE FROM files WHERE path = ?", (path,))

    async def file_mtimes(self) -> dict[str, float]:
        """已索引文件的 path -> mtime 映射。"""
        rows = await self.query("SELECT path, mtime FROM files")
        return {path: mtime for path, mtime in rows}

    async def is_indexed(self) -> bool:
        """索引是否已构建（files 表非空）。"""
        rows = await self.query("SELECT COUNT(*) FROM files")
        return bool(rows) and rows[0][0] > 0

    async def set_file_mtime(self, path: str, mtime: float) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO files (path, mtime) VALUES (?, ?)",
            (path, mtime),
        )

    async def clear(self) -> None:
        """清空全部块与索引（含 FTS 重建）。"""
        conn = await self._get_conn()

        def _run():
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM files")
            conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
            conn.commit()

        await asyncio.to_thread(_run)
