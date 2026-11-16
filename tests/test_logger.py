"""日志格式化的回归测试。"""

from __future__ import annotations

import logging

from ..infra.logger import SingleLineFormatter


def make_record(message: str) -> logging.LogRecord:
    return logging.LogRecord("test", logging.ERROR, __file__, 1, message, None, None)


def test_single_line_formatter_escapes_newlines():
    formatter = SingleLineFormatter("%(message)s")

    assert formatter.format(make_record("boom\nTraceback:\n  at x")) == "boom\\nTraceback:\\n  at x"


def test_single_line_formatter_keeps_plain_message():
    formatter = SingleLineFormatter("%(message)s")

    assert formatter.format(make_record("ok")) == "ok"
