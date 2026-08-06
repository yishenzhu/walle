"""SQLite 异步存储基类。

封装懒连接与 asyncio.to_thread 样板。子类只需实现 _connect()（建表）与业务方法。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from ..conf import auto_path


class SQLiteStore:
    def __init__(self, db_path: str):
        self._db_path = auto_path(db_path)
        self._conn: sqlite3.Connection | None = None

    async def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = await asyncio.to_thread(self._connect)
        return self._conn

    def _connect(self) -> sqlite3.Connection:
        """子类覆盖：建目录、连接、建表。"""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.commit()
        return conn

    async def execute(self, sql: str, params: tuple = ()) -> None:
        """执行写操作（INSERT/UPDATE/DELETE）。"""
        conn = await self._get_conn()

        def _run():
            conn.execute(sql, params)
            conn.commit()

        await asyncio.to_thread(_run)

    async def executemany(self, sql: str, seq: list[tuple]) -> None:
        """批量执行写操作。"""
        conn = await self._get_conn()

        def _run():
            conn.executemany(sql, seq)
            conn.commit()

        await asyncio.to_thread(_run)

    async def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        """执行查询，返回行列表。"""
        conn = await self._get_conn()

        def _run():
            return conn.execute(sql, params).fetchall()

        return await asyncio.to_thread(_run)

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None
