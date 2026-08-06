"""Obsidian 笔记解析器。

将 Markdown 笔记解析为"叶子块 + 祖先链"结构：
- 每个标题小节是一个叶子块（一条知识原子），heading 为该小节标题
- ancestors 记录从 H1 到父级标题的祖先链，检索时携带上下文
- 切分边界 = 标题边界（语义边界），因此代码块/表格天然不会被截断
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel, Field

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


class Chunk(BaseModel):
    path: str                                  # vault 内相对路径
    heading: str                               # 叶子小节标题（文件开头为 ""）
    ancestors: list[str] = Field(default_factory=list)  # 祖先标题链 [H1, H2, ...]
    content: str                               # 叶子内容
    search_text: str = ""                      # 分词后的检索文本（索引器填充）
    tags: list[str] = Field(default_factory=list)
    frontmatter: dict[str, Any] = Field(default_factory=dict)

    @property
    def heading_path(self) -> str:
        """完整标题路径，如 "生产库 / 连接方式"。"""
        parts = [*self.ancestors, self.heading] if self.heading else self.ancestors
        return " / ".join(parts)


def parse_note(path: Path, rel_path: str) -> list[Chunk]:
    post = frontmatter.load(path)
    fm = dict(post.metadata) if isinstance(post.metadata, dict) else {}
    body = post.content
    raw_tags = fm.get("tags", [])
    if isinstance(raw_tags, str):
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
    elif isinstance(raw_tags, list):
        tags = [t for t in raw_tags if isinstance(t, str)]
    else:
        tags = []

    chunks: list[Chunk] = []
    stack: list[tuple[int, str]] = []          # (标题级别, 标题文本)
    buf: list[str] = []

    def flush():
        nonlocal buf
        content = "\n".join(buf).strip()
        if content:
            heading = stack[-1][1] if stack else ""
            chunks.append(
                Chunk(
                    path=rel_path,
                    heading=heading,
                    ancestors=[t for _, t in stack[:-1]],
                    content=content,
                    tags=list(tags),
                    frontmatter=dict(fm),
                )
            )
        buf = []

    for line in body.split("\n"):
        m = _HEADING_RE.match(line)
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            buf.append(line)
    flush()
    return chunks
