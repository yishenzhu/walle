from __future__ import annotations

import asyncio
import fnmatch
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel, Field, model_validator

from ..conf import AGENT_DIR
from ..infra import Skill, Tool


class ToolFilter(BaseModel):
    """工具筛选配置：allow/deny glob 列表，deny 优先于 allow。"""

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)

    def allows(self, name: str) -> bool:
        if any(fnmatch.fnmatchcase(name, pat) for pat in self.deny):
            return False
        return any(fnmatch.fnmatchcase(name, pat) for pat in self.allow)

    def apply(self, tools: list[Tool]) -> list[Tool]:
        return [t for t in tools if self.allows(t.name)]


class Handoff(BaseModel):
    target: Agent

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="after")
    def _validate_target(self) -> Handoff:
        self.target._validate_tool_metadata()
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
    # 工具筛选配置：运行时从插件 runner 取全部工具后按名字过滤
    # （allow/deny glob，deny 优先）
    tool_filter: ToolFilter = Field(default_factory=ToolFilter)
    # 技能白名单：[] 不注入 / ["*"] 全部 / 列表仅命中项
    skills: list[str] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    def skill_prompt(self, skills: Sequence[Skill]) -> str:
        """从会话可用技能拼清单（一行一技能）。"""
        if not self.skills or not skills:
            return ""
        wanted = None if "*" in self.skills else set(self.skills)
        lines = [
            f"- {s.name}: {s.description}（{s.path}）"
            for s in skills
            if wanted is None or s.name in wanted
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
        """按名加载 Agent（.agent/agents/<name>.md）。"""
        name = name or "default"
        root = root or AGENT_DIR / "agents"
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

    def _validate_tool_metadata(self) -> None:
        if self.name is None or self.description is None:
            raise ValueError("Agent must have a name and description")

    def as_tool(self, env):
        """派发子 agent 的工具：隔离历史，继承 provider/tools/cwd/agents/bus。"""
        self._validate_tool_metadata()
        agent = self

        async def fn(input: str) -> str:
            from .runner import Runner, SessionContext

            if env.history_factory is None:
                raise RuntimeError(
                    "SessionContext.history_factory is required to dispatch subagents"
                )
            child = SessionContext(
                history=env.history_factory(),  # 子 agent 独立历史，不继承父对话
                provider=env.provider,
                tools=env.tools,
                cwd=env.cwd,
                agents=env.agents,
                depth=env.depth + 1,
                bus=env.bus,  # 继承总线：治理钩子（审批/preflight）不绕过
                history_factory=env.history_factory,
            )
            # 独立 task：子 run 对 tool_context 的写入不泄漏到父的同批工具调用
            result = await asyncio.create_task(Runner().run(agent, input, env=child))
            return str(result.output or "")

        return Tool.from_function(
            fn, name=f"call_{self.name}", description=self.description or ""
        )
