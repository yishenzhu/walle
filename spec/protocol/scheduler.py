"""会话调度能力：插件可在会话空闲时追加一次 run。"""

from __future__ import annotations

import asyncio
from typing import Protocol


class Scheduler(Protocol):
    """提交一次后台 run；忙或已关闭时返回 None。"""

    @property
    def has_model(self) -> bool: ...

    @property
    def last_error(self) -> str | None: ...

    def submit_background(self, content: str) -> asyncio.Task | None: ...
