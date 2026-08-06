"""工具使用统计存储。

记录每个工具的使用次数与最后使用时间，供工具分级（hot/cold）使用。
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

from ..conf import auto_path

DEFAULT_DB_PATH = "data/tool_usage.db"


class ToolUsage:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self._db_path = auto_path(db_path)
        self._conn: sqlite3.Connection | None = None

    async def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = await asyncio.to_thread(self._connect)
        return self._conn

    def _connect(self) -> sqlite3.Connection:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_meta (
                name TEXT PRIMARY KEY,
                description TEXT NOT NULL DEFAULT '',
                usage_count INTEGER NOT NULL DEFAULT 0,
                last_used_at REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'hot'
            )
            """
        )
        conn.commit()
        return conn

    async def record(self, name: str, description: str = "") -> None:
        """记录一次工具调用。"""
        conn = await self._get_conn()

        def _run():
            conn.execute(
                """
                INSERT INTO tool_meta (name, description, usage_count, last_used_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(name) DO UPDATE SET
                    usage_count = usage_count + 1,
                    last_used_at = excluded.last_used_at,
                    description = excluded.description
                """,
                (name, description, time.time()),
            )
            conn.commit()

        await asyncio.to_thread(_run)

    async def get(self, name: str) -> dict | None:
        """查询单个工具的统计。"""
        conn = await self._get_conn()

        def _run():
            row = conn.execute(
                "SELECT name, description, usage_count, last_used_at, status FROM tool_meta WHERE name = ?",
                (name,),
            ).fetchone()
            if row is None:
                return None
            return {
                "name": row[0],
                "description": row[1],
                "usage_count": row[2],
                "last_used_at": row[3],
                "status": row[4],
            }

        return await asyncio.to_thread(_run)

    async def all(self) -> list[dict]:
        """查询全部工具的统计（按使用次数降序）。"""
        conn = await self._get_conn()

        def _run():
            rows = conn.execute(
                "SELECT name, description, usage_count, last_used_at, status FROM tool_meta ORDER BY usage_count DESC"
            ).fetchall()
            return [
                {
                    "name": r[0],
                    "description": r[1],
                    "usage_count": r[2],
                    "last_used_at": r[3],
                    "status": r[4],
                }
                for r in rows
            ]

        return await asyncio.to_thread(_run)

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None
