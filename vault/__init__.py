"""基于 Obsidian Vault 的本地知识库（纯文件系统方案）。"""

from .parser import Chunk, parse_note
from .retriever import Retriever

__all__ = ["Chunk", "parse_note", "Retriever"]
