"""切点持久化：每会话一行切点（SQLite）。

new_window 硬切后把切点落盘，重启恢复投影——避免重启后折叠失效、
全文重新暴露。底层原文在 messages 表，这里只存一个切点整数。
"""

import sqlite3

from ..infra import SQLiteStore


class SQLiteProjectionStore(SQLiteStore):
    """持久化切点：表为 (session_id PRIMARY KEY, cut INTEGER)，一行一会话。"""

    def __init__(self, db_path: str, session_id: str = "default"):
        super().__init__(db_path)
        self._session_id = session_id

    def _connect(self) -> sqlite3.Connection:
        conn = super()._connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projection (
                session_id TEXT PRIMARY KEY,
                cut INTEGER NOT NULL
            )
            """
        )
        conn.commit()
        return conn

    async def load(self) -> int | None:
        rows = await self.query(
            "SELECT cut FROM projection WHERE session_id = ?",
            (self._session_id,),
        )
        return int(rows[0][0]) if rows else None

    async def save(self, cut: int) -> None:
        await self.execute(
            "INSERT INTO projection (session_id, cut) VALUES (?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET cut = excluded.cut",
            (self._session_id, cut),
        )

    async def clear(self) -> None:
        await self.execute(
            "DELETE FROM projection WHERE session_id = ?", (self._session_id,)
        )
