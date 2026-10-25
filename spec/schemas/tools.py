"""工具作用域：一个作用域对"它继承来的工具"的 allow/deny。

与 harness 的 `ToolRestriction` 同义：同一份 restriction 既决定喂模型的 schema，
也决定执行是否被拒——**one visibility**（看不见的也执行不了）。
值类型，落在 schemas（配置数据），判定逻辑由 ToolRuntime 实现。

语义（对齐 harness）：
- `deny` 命中即拒，**优先于 allow**（"摘掉某工具"的唯一手段，可摘掉自己的注册）。
- `allow=None`（缺省）表示**全收**（只受 deny 约束）；`allow=[]` 表示**全拒**；
  `allow=[...]` 表示只放行命中项。故 deny-only 的过滤"放行未列出的"。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ToolRestriction(BaseModel):
    """工具可见性限制；多层限制**取交集**（都不拒绝才可见）。

    `allow=None` = 全收；`allow=[]` = 全拒；模式为 fnmatch 风格（支持 `mcp_obsidian*`）。
    """

    allow: list[str] | None = None
    deny: list[str] = Field(default_factory=list)
