"""笔记工具：检索笔记。读写由 MCP（Obsidian Local REST API）提供。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ...vault import Retriever


def make_search_notes(retriever: Retriever) -> Callable[[str], Awaitable[str]]:
    """构建检索笔记工具（闭包捕获检索器，由 ToolRegistry 装配时调用）。"""

    async def search_notes(query: str) -> str:
        """检索相关条目。"""
        results = await retriever.retrieve(query, k=3)
        if not results:
            return "未找到相关笔记"
        lines = []
        for r in results:
            lines.append(f"来源: {r.path} :: {r.heading_path}\n{r.content}")
        return "\n\n".join(lines)

    return search_notes
