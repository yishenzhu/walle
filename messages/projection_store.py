"""切点持久化：文件后端（projection.json）与 SQLite 后端。"""

import asyncio
import sqlite3
from pathlib import Path

from ..infra import SQLiteStore
from ..infra.jsonio import read_json, write_json_atomic

PROJECTION_FILE = "projection.json"


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
        rows = await self.fetchall(
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


class FileProjectionStore:
    """切点持久化：会话目录下 projection.json。"""

    def __init__(self, path: str | Path):
        self._path = Path(path)

    async def load(self) -> int | None:
        def read_cut() -> int | None:
            data = read_json(self._path)
            cut = data.get("cut") if data else None
            if isinstance(cut, bool) or not isinstance(cut, int) or cut < 0:
                return None
            return cut

        return await asyncio.to_thread(read_cut)

    async def save(self, cut: int) -> None:
        if cut < 0:
            raise ValueError("cut must be >= 0")
        await asyncio.to_thread(write_json_atomic, self._path, {"cut": cut})

    async def clear(self) -> None:
        def remove() -> None:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass

        await asyncio.to_thread(remove)
