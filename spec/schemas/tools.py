"""工具作用域：一个作用域对"它继承来的工具"的 allow/deny。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ToolRestriction(BaseModel):
    """工具可见性限制；多层限制**取交集**（都不拒绝才可见）。"""

    allow: list[str] | None = None
    deny: list[str] = Field(default_factory=list)
