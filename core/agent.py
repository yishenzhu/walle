from __future__ import annotations

import fnmatch
import functools
from pathlib import Path
from typing import TypeVar, Generic, Any, Callable

import frontmatter
import yaml
from pydantic import BaseModel, Field, create_model, model_validator
from ..conf import DOT_AGENT
from ..tools import Tool

TContext = TypeVar("TContext")

# 简写类型名 → Python 类型
TYPE_MAP = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "any": Any,
    "object": dict,
    "array": list,
}


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
        root: Path | None = None,
    ) -> Agent:
        """按名加载 Agent（.agent/agents/<name>.md）。

        frontmatter 支持 name/description/temperature/tools/output_model，
        正文即 instruction。root 缺省用 DOT_AGENT/agents。
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
        output_model = meta.get("output_model")
        models_path = root / "models.yaml"
        output_type = (
            cls._load_model(output_model, models_path) if output_model else None
        )
        return cls(
            name=name,
            description=meta.get("description"),
            instruction=instruction,
            temperature=meta.get("temperature"),
            output_type=output_type,
            tool_filter=ToolFilter.model_validate(meta.get("tools") or {}),
            tools=tools,
        )

    @staticmethod
    @functools.cache
    def _load_model(model_name: str, path: Path) -> type[BaseModel] | None:
        """从 path（models.yaml）加载名为 model_name 的输出模型，按参数缓存。

        path 为模型文件完整路径。文件缺失或条目不存在抛 ValueError。
        """
        if not path.exists():
            raise ValueError(f"models file not found: {path}")
        registry = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        spec = registry.get(model_name)
        if spec is None:
            raise ValueError(f"输出模型 {model_name} 不存在于 {path}")
        return Agent._build_model(model_name, spec)

    @classmethod
    def _build_model(cls, name: str, spec: dict, depth: int = 0) -> type[BaseModel]:
        """把字段 dict 转成 Pydantic 模型。

        简写（类型字符串）或完整 dict（type/desc/default/items）；array 用
        items 定元素类型，object 嵌套递归，default 非 None 则字段可选。
        """
        max_depth = 32  # 防御自引用/过深嵌套导致死循环
        if depth > max_depth:
            raise ValueError(f"模型嵌套过深（>{max_depth}），疑似自引用：{name}")
        fields = {}
        for fname, fspec in spec.items():
            if isinstance(fspec, dict):
                ftype = fspec.get("type")
                desc = fspec.get("desc")
                default = fspec.get("default")
                if ftype == "array":
                    py_type = list[cls._build_type(fspec.get("items"))]
                elif ftype == "object":
                    py_type = cls._build_model(
                        fname, fspec.get("properties", {}), depth + 1
                    )
                else:
                    py_type = cls._build_type(ftype)
                if default is not None:
                    field_default = (
                        Field(default=default, description=desc)
                        if desc
                        else Field(default=default)
                    )
                else:
                    field_default = Field(description=desc) if desc else ...
                fields[fname] = (py_type, field_default)
            else:
                py_type = cls._build_type(fspec)
                fields[fname] = (py_type, ...)
        return create_model(f"output_{name}", **fields)

    @staticmethod
    def _build_type(tspec: Any) -> Any:
        """简写类型名 → Python 类型。

        null（YAML 转为 None）→ type(None)；联合类型 list 暂不支持，抛错。
        """
        if tspec is None:
            return type(None)
        if isinstance(tspec, list):
            raise ValueError(f"联合类型暂不支持: {tspec}")
        m = TYPE_MAP.get(tspec)
        if m is None:
            raise ValueError(f"未知类型: {tspec}")
        return m

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
            from ..messages import InMemoryMessages

            # 嵌套 Runner 显式构造隔离环境（独立历史）：与父执行实体互不污染
            runner = Runner()
            env = SessionEnv(messages=InMemoryMessages())
            result = await runner.run(agent, input, env=env)
            return result.output

        return Tool.from_function(
            fn, name=f"call_{self.name}", description=self.description
        )
