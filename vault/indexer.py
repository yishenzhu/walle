"""Obsidian 笔记索引器。

扫描 vault 目录，把 .md 笔记解析为块写入 Store。
支持：
- full_build(): 全量重建索引
- refresh():    按 mtime 增量刷新（只重建变化的文件，移除已删除的文件）
"""

from __future__ import annotations

import logging
from pathlib import Path

from .parser import parse_note
from .store import Store
from .tokenize import tokenize

logger = logging.getLogger(__name__)


class Indexer:
    def __init__(self, vault: str, store: Store):
        self._root = Path(vault)
        self._store = store

    def _md_files(self) -> list[Path]:
        return sorted(
            p
            for p in self._root.rglob("*.md")
            if p.is_file() and ".obsidian" not in p.parts
        )

    async def _index_one(self, rel: str, p: Path) -> None:
        try:
            chunks = parse_note(p, rel)
            for c in chunks:
                c.search_text = tokenize(" ".join([*c.ancestors, c.heading, c.content]))
            await self._store.upsert_file(rel, chunks)
            await self._store.set_file_mtime(rel, p.stat().st_mtime)
        except Exception as e:
            logger.warning(f"index failed ({rel}): {e}")

    async def full_build(self) -> None:
        await self._store.clear()
        files = self._md_files()
        logger.info(f"vault full build: {len(files)} files")
        for p in files:
            await self._index_one(p.relative_to(self._root).as_posix(), p)

    async def refresh(self) -> None:
        """增量刷新：重建 mtime 变化的文件，移除已删除的文件。"""
        files = {p.relative_to(self._root).as_posix(): p for p in self._md_files()}
        indexed = await self._store.file_mtimes()

        for rel in set(indexed) - set(files):
            logger.info(f"vault remove: {rel}")
            await self._store.delete_file(rel)

        for rel, p in files.items():
            if rel not in indexed or indexed[rel] != p.stat().st_mtime:
                await self._index_one(rel, p)
