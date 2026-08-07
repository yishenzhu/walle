"""vault 知识库：装配检索器、笔记检索工具、生命周期管理。"""

from __future__ import annotations

from ..conf import VaultConfig
from .retriever import Retriever
from .store import Store, DEFAULT_DB_PATH
from .indexer import Indexer


class Vault:
    """Obsidian 笔记库：建索引、检索笔记、关闭 store。"""

    def __init__(self, conf: VaultConfig):
        self._store = Store(conf.db_path or DEFAULT_DB_PATH)
        self._indexer = Indexer(conf.path, self._store)
        self._retriever = Retriever(self._store, self._indexer)

    async def setup(self) -> None:
        """建索引。"""
        await self._indexer.ensure_indexed()

    async def search_notes(self, query: str) -> str:
        """搜索笔记库，返回相关笔记片段及来源。优先使用此工具查找笔记内容。"""
        results = await self._retriever.retrieve(query, k=3)
        if not results:
            return "未找到相关笔记"
        lines = [f"来源: {r.path} :: {r.heading_path}\n{r.content}" for r in results]
        return "\n\n".join(lines)

    async def close(self) -> None:
        await self._store.close()
