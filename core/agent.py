from __future__ import annotations

from typing import TypeVar, Generic, Any, Callable
from pydantic import BaseModel, Field, model_validator
from ..tools import Tool

TContext = TypeVar("TContext")


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
    model: str | None = None
    instruction: str | None = None
    handoffs: list[Handoff] = Field(default_factory=list)
    temperature: float | None = None
    output_type: type[BaseModel] | None = None
    # 工具源：返回该 Agent 当前全部工具（运行时添加的工具由此实时反映）
    tools: Callable[[], list[Tool]] | None = None

    model_config = {"arbitrary_types_allowed": True}

    def _validate_as_tool(self) -> None:
        if self.name is None or self.description is None:
            raise ValueError("Agent must have a name and description")

    def as_tool(self) -> Tool:
        self._validate_as_tool()
        agent = self

        async def fn(input: str):
            from .runner import Runner

            # 嵌套 Runner 拥有自己的 kernel：与父执行实体隔离，互不污染
            runner = Runner()
            result = await runner.run(agent, input)
            return result.output

        return Tool.from_function(
            fn, name=f"call_{self.name}", description=self.description
        )
