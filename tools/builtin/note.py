"""笔记工具：Obsidian 笔记语义检索。读写由 MCP（Obsidian Local REST API）提供。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ...vault import Retriever


def make_semantic_search(retriever: Retriever) -> Callable[[str], Awaitable[str]]:
    """构建语义检索工具（闭包捕获检索器）。"""

    async def semantic_search(query: str) -> str:
        """在 Obsidian 笔记中做语义检索。"""
        results = await retriever.retrieve(query, k=3)
        if not results:
            return "未找到相关笔记"
        lines = []
        for r in results:
            lines.append(f"来源: {r.path} :: {r.heading_path}\n{r.content}")
        return "\n\n".join(lines)

    return semantic_search
