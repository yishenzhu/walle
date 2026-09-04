from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel, Field, model_validator
from ..conf import DOT_AGENT
from ..infra import Skill, Tool


class ToolFilter(BaseModel):
    """工具筛选配置：allow/deny glob 列表，deny 优先于 allow。

    allow 默认 []（禁用全部）；给出显式模式时仅保留命中 allow
    且未命中 deny 的工具。模式为 fnmatch 风格（支持 mcp_obsidian*）。
    """

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)

    def includes(self, name: str) -> bool:
        if any(fnmatch.fnmatchcase(name, pat) for pat in self.deny):
            return False
        return any(fnmatch.fnmatchcase(name, pat) for pat in self.allow)

    def apply(self, tools: list[Tool]) -> list[Tool]:
        return [t for t in tools if self.includes(t.name)]


class Handoff(BaseModel):
    target: Agent

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="after")
    def _validate_target(self) -> Handoff:
        self.target._validate_as_tool()
        return self

    def __str__(self) -> str:
        return f"Transfer to {self.target.name}: ({self.target.description})"

    def as_tool(self) -> Tool:
        async def fn(args: dict[str, Any]) -> Handoff:
            return self

        return Tool(
            name=f"transfer_to_{self.target.name}",
            description=str(self),
            parameters={"type": "object", "properties": {}},
            fn=fn,
        )


class Agent(BaseModel):
    name: str | None = None
    description: str | None = None
    instruction: str | None = None
    handoffs: list[Handoff] = Field(default_factory=list)
    temperature: float | None = None
    output_type: type[BaseModel] | None = None
    # 工具筛选配置：运行时从扩展 runner 取全部工具后按名字过滤
    # （allow/deny glob，deny 优先）
    tool_filter: ToolFilter = Field(default_factory=ToolFilter)
    # 技能白名单：[] 不注入 / ["*"] 全部 / 列表仅命中项
    skills: list[str] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    def skill_prompt(self, skills: dict[str, Skill]) -> str:
        """从会话可用技能拼清单（一行一技能）。

        skills 为会话激活扩展收集的技能（name→Skill）。白名单 self.skills：
        空 = 不注入；["*"] = 全部；列表 = 只注入命中项。
        """
        if not self.skills or not skills:
            return ""
        wanted = None if "*" in self.skills else set(self.skills)
        lines = [
            f"- {m.name}: {m.description}（{m.path}）"
            for m in skills.values()
            if wanted is None or m.name in wanted
        ]
        if not lines:
            return ""
        return "可用技能（任务匹配时加载对应技能后执行）：\n" + "\n".join(lines)

    @classmethod
    def load(
        cls,
        name: str | None = None,
        root: Path | None = None,
    ) -> Agent:
        """按名加载 Agent（.agent/agents/<name>.md）。

        frontmatter 支持 name/description/temperature/skills/tools(筛选)，
        正文即 instruction。root 缺省用 DOT_AGENT/agents。工具不经 agent
        配置——运行时由会话扩展 runner 提供。
        """
        name = name or "default"
        root = root or DOT_AGENT / "agents"
        path = root / f"{name}.md"
        if not path.exists():
            raise ValueError(f"agent file not found: {path}")
        post = frontmatter.load(path)
        meta = post.metadata
        if meta.get("name") != name:
            raise ValueError(
                f"agent name '{meta.get('name')}' not match file name '{name}'"
            )
        instruction = post.content.strip() or None
        return cls(
            name=name,
            description=meta.get("description"),
            instruction=instruction,
            temperature=meta.get("temperature"),
            skills=meta.get("skills") or [],
            tool_filter=ToolFilter.model_validate(meta.get("tools") or {}),
        )

    def available_tools(self, source: list[Tool]) -> list[Tool]:
        """从会话工具源过滤出本 agent 可用工具（tool_filter allow/deny）。"""
        return self.tool_filter.apply(source)

    def _validate_as_tool(self) -> None:
        if self.name is None or self.description is None:
            raise ValueError("Agent must have a name and description")

    def as_tool(self) -> Tool:
        self._validate_as_tool()
        agent = self

        async def fn(input: str):
            from .runner import Runner, SessionContext
            from ..messages import InMemoryMessages

            # 嵌套 Runner 显式构造隔离环境（独立历史）：与父执行实体互不污染
            runner = Runner()
            env = SessionContext(messages=InMemoryMessages())
            result = await runner.run(agent, input, env=env)
            return result.output

        return Tool.from_function(
            fn, name=f"call_{self.name}", description=self.description
        )
