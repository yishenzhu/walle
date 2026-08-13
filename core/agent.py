from __future__ import annotations

import fnmatch
from typing import TypeVar, Generic, Any, Callable

import frontmatter
from pydantic import BaseModel, Field, model_validator
from ..conf import DOT_AGENT
from ..tools import Tool

TContext = TypeVar("TContext")


class ToolFilter(BaseModel):
    """工具筛选配置：allow/deny glob 列表，deny 优先于 allow。

    allow 默认 ["*"]（全放行）；给出显式模式时仅保留命中 allow
    且未命中 deny 的工具。模式为 fnmatch 风格（支持 mcp_obsidian*）。
    """

    allow: list[str] = Field(default_factory=lambda: ["*"])
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


class Agent(BaseModel, Generic[TContext]):
    name: str | None = None
    description: str | None = None
    instruction: str | None = None
    handoffs: list[Handoff] = Field(default_factory=list)
    temperature: float | None = None
    output_type: type[BaseModel] | None = None
    # 工具源：返回该 Agent 当前全部工具（运行时添加的工具由此实时反映）
    tools: Callable[[], list[Tool]] | None = None
    # 工具筛选配置：从 tools 源中按名字过滤（allow/deny glob，deny 优先）
    tool_filter: ToolFilter = Field(default_factory=ToolFilter)

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def load(
        cls,
        name: str | None = None,
        tools: Callable[[], list[Tool]] | None = None,
    ) -> "Agent":
        """按 agent 名从 frontmatter 加载 Agent（.agent/agents/<name>.md）。

        frontmatter 支持：name / description / temperature /
        tools(allow, deny)。markdown 正文即 instruction。
        未提供 name 时加载 default；文件不存在或 name 与文件名不符时抛 ValueError。
        """
        name = name or "default"
        path = DOT_AGENT / "agents" / f"{name}.md"
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
            tool_filter=ToolFilter.model_validate(meta.get("tools") or {}),
            tools=tools,
        )

    def available_tools(self) -> list[Tool]:
        """当前可用的工具：实时取 tools 源并应用 tool_filter 筛选。"""
        return self.tool_filter.apply(self.tools()) if self.tools else []

    def _validate_as_tool(self) -> None:
        if self.name is None or self.description is None:
            raise ValueError("Agent must have a name and description")

    def as_tool(self) -> Tool:
        self._validate_as_tool()
        agent = self

        async def fn(input: str):
            from .runner import Runner, SessionEnv
            from ..infra import PyKernel
            from ..messages import InMemoryMessages

            # 嵌套 Runner 显式构造隔离环境（独立 kernel + 历史）：与父执行实体互不污染
            runner = Runner()
            env = SessionEnv(kernel=PyKernel(), messages=InMemoryMessages())
            result = await runner.run(agent, input, env=env)
            return result.output

        return Tool.from_function(
            fn, name=f"call_{self.name}", description=self.description
        )
