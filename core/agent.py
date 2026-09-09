from __future__ import annotations

import asyncio
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
    # 可移交 / 可派发的目标 agent 名（运行时按会话 agent 注册表解析构造工具）
    handoffs: list[str] = Field(default_factory=list)
    subagents: list[str] = Field(default_factory=list)
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
        return (
            "Available skills (load the matching skill before proceeding):\n"
            + "\n".join(lines)
        )

    @classmethod
    def load(
        cls,
        name: str | None = None,
        root: Path | None = None,
    ) -> Agent:
        """按名加载 Agent（.agent/agents/<name>.md）。

        frontmatter 支持 name/description/temperature/skills/tools(筛选)/
        handoffs/subagents(目标 agent 名)，正文即 instruction。工具不经
        agent 配置——运行时由会话扩展 runner 提供。
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
            handoffs=meta.get("handoffs") or [],
            subagents=meta.get("subagents") or [],
            tool_filter=ToolFilter.model_validate(meta.get("tools") or {}),
        )

    def available_tools(self, source: list[Tool]) -> list[Tool]:
        """从会话工具源过滤出本 agent 可用工具（tool_filter allow/deny）。"""
        return self.tool_filter.apply(source)

    def _validate_as_tool(self) -> None:
        if self.name is None or self.description is None:
            raise ValueError("Agent must have a name and description")

    def as_tool(self, env):
        """派发子 agent 的工具：隔离历史，继承 provider/ext/cwd/agents/bus。

        深度由子环境 +1 承担，供 runner 卡住递归。
        子 agent headless：不继承 channel（无 ask_user / 交互式审批）。
        """
        self._validate_as_tool()
        agent = self

        async def fn(input: str) -> str:
            from .runner import Runner, SessionContext
            from ..messages import InMemoryMessages

            child = SessionContext(
                history=InMemoryMessages(),  # 子 agent 独立历史，不继承父对话
                provider=env.provider,
                ext_runner=env.ext_runner,
                cwd=env.cwd,
                agents=env.agents,
                depth=env.depth + 1,
                bus=env.bus,  # 继承总线：治理钩子（审批/preflight）不绕过
            )
            # 独立 task：子 run 对 tool_context 的写入不泄漏到父的同批工具调用
            result = await asyncio.create_task(Runner().run(agent, input, env=child))
            return str(result.output or "")

        return Tool.from_function(
            fn, name=f"call_{self.name}", description=self.description or ""
        )
