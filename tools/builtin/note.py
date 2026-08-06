"""笔记工具：检索笔记、写笔记。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ...vault import Retriever
from ...vault.writer import NoteWriter


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


def make_write_note(writer: NoteWriter) -> Callable[[str, str], Awaitable[str]]:
    """构建写笔记工具（闭包捕获写入器，由 ToolRegistry 装配时调用）。"""

    async def write_note(path: str, content: str) -> str:
        """path 为笔记相对路径"""
        try:
            return await writer.write(path, content)
        except ValueError as e:
            return f"写入失败: {e}"

    return write_note
