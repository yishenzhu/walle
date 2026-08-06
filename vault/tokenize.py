"""中文分词工具。

用 jieba 把文本切成词（空格连接），供 FTS5 索引与检索。
jieba 不可用时降级返回原文（英文检索仍可用）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import jieba

    def tokenize(text: str) -> str:
        return " ".join(jieba.cut(text, cut_all=False))
except ImportError:
    logger.warning("jieba not installed, fallback to raw text")

    def tokenize(text: str) -> str:
        return text
