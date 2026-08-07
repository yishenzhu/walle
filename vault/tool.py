"""vault 检索工具：装配检索器并暴露为可供 Agent 注册的工具。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from ..conf import VaultConfig
from .retriever import Retriever
from .store import Store, DEFAULT_DB_PATH
from .indexer import Indexer


@asynccontextmanager
async def make_search_notes(conf: VaultConfig) -> AsyncIterator[Callable[[str], Awaitable[str]]]:
    """装配 vault 检索器并返回笔记检索工具。

    传入 vault 配置；内部建索引；退出时自动关闭 store（RAII 风格）。
    """
    store = Store(conf.db_path or DEFAULT_DB_PATH)
    indexer = Indexer(conf.path, store)
    await indexer.ensure_indexed()
    retriever = Retriever(store, indexer)

    async def search_notes(query: str) -> str:
        """搜索笔记库，返回相关笔记片段及来源。优先使用此工具查找笔记内容。"""
        results = await retriever.retrieve(query, k=3)
        if not results:
            return "未找到相关笔记"
        lines = [f"来源: {r.path} :: {r.heading_path}\n{r.content}" for r in results]
        return "\n\n".join(lines)

    try:
        yield search_notes
    finally:
        await store.close()
