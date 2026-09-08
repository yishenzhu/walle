import asyncio
import sqlite3
from collections.abc import Sequence
from ..infra import SQLiteStore
from ..schemas import Message, MessageAdapter, Usage


class SQLiteMessages(SQLiteStore):
    def __init__(self, db_path: str = "data/session.db", session_id: str = "default"):
        super().__init__(db_path)
        self._session_id = session_id

    def _connect(self) -> sqlite3.Connection:
        conn = super()._connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at REAL NOT NULL DEFAULT (julianday('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages (session_id)"
        )
        conn.commit()
        return conn

    async def get(self, limit: int | None = None) -> list[Message]:
        conn = await self._get_conn()

        def _query():
            sql = "SELECT data FROM messages WHERE session_id = ? ORDER BY id ASC"
            params: list[str | int] = [self._session_id]
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [MessageAdapter.validate_json(row[0]) for row in rows]

        return await asyncio.to_thread(_query)

    async def query(self, offset: int = 0, limit: int = 20) -> list[Message]:
        """窗口查询：按 id 顺序取 [offset, offset+limit) 的原文。"""
        if offset < 0 or limit < 0:
            raise ValueError("offset/limit must be >= 0")
        conn = await self._get_conn()

        def _query():
            sql = (
                "SELECT data FROM messages WHERE session_id = ? "
                "ORDER BY id ASC LIMIT ? OFFSET ?"
            )
            rows = conn.execute(sql, (self._session_id, limit, offset)).fetchall()
            return [MessageAdapter.validate_json(row[0]) for row in rows]

        return await asyncio.to_thread(_query)

    async def search(self, query: str = "", limit: int = 20) -> list[tuple[int, Message]]:
        """关键词检索：词间 AND 的 LIKE 子串匹配，返回 (绝对序号, 消息) 倒序。"""
        words = [w for w in query.split() if w]
        conn = await self._get_conn()

        def _search():
            # 会话内先按 id 排号（seq），再做词过滤；seq 同 query 的 offset 寻址
            sql = (
                "WITH ranked AS ("
                "  SELECT data, ROW_NUMBER() OVER (ORDER BY id) - 1 AS seq "
                "  FROM messages WHERE session_id = ?"
                ")"
                "SELECT data, seq FROM ranked WHERE 1=1"
                + "".join(" AND LOWER(data) LIKE ?" for _ in words)
                + " ORDER BY seq DESC LIMIT ?"
            )
            params: list[str | int] = [self._session_id]
            params += [f"%{w.lower()}%" for w in words]
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [
                (int(row[1]), MessageAdapter.validate_json(row[0])) for row in rows
            ]

        return await asyncio.to_thread(_search)

    async def count(self) -> int:
        conn = await self._get_conn()

        def _count():
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                (self._session_id,),
            ).fetchone()
            return int(row[0])

        return await asyncio.to_thread(_count)

    async def add(self, items: Sequence[Message], usage: Usage | None = None):
        if not items:
            return
        conn = await self._get_conn()

        def _insert():
            conn.executemany(
                "INSERT INTO messages (session_id, data) VALUES (?, ?)",
                [(self._session_id, item.model_dump_json()) for item in items],
            )
            conn.commit()

        await asyncio.to_thread(_insert)

    async def pop(self) -> Message | None:
        conn = await self._get_conn()

        def _pop():
            row = conn.execute(
                "SELECT id, data FROM messages WHERE session_id = ? ORDER BY id ASC LIMIT 1",
                (self._session_id,),
            ).fetchone()
            if row is None:
                return None
            msg_id, data = row
            conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
            conn.commit()
            return MessageAdapter.validate_json(data)

        return await asyncio.to_thread(_pop)

    async def clear(self):
        await self.execute(
            "DELETE FROM messages WHERE session_id = ?",
            (self._session_id,),
        )
