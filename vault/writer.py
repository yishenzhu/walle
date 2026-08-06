"""笔记写入器：安全地把笔记写入 vault 并刷新索引。"""

from __future__ import annotations

import logging
from pathlib import Path

from .indexer import Indexer

logger = logging.getLogger(__name__)


class NoteWriter:
    def __init__(self, vault: str, indexer: Indexer):
        self._root = Path(vault).resolve()
        self._indexer = indexer

    def _safe_path(self, rel: str) -> Path:
        """把相对路径解析为 vault 内绝对路径；越界/非法则抛 ValueError。"""
        p = (self._root / rel).resolve()
        if not p.is_relative_to(self._root):
            raise ValueError(f"path escapes vault: {rel}")
        if ".obsidian" in p.parts:
            raise ValueError("cannot write into .obsidian")
        if p.suffix != ".md":
            raise ValueError(f"not a markdown file: {rel}")
        return p

    async def write(self, rel: str, content: str) -> str:
        """创建或覆盖一篇笔记，返回其 vault 相对路径。"""
        p = self._safe_path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        await self._indexer.refresh()
        return p.relative_to(self._root).as_posix()
