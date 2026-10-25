"""插件运行时适配层：把 Context/Service 能力桥接到现有会话运行时。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..conf import ToolConfig
from ..infra import PluginRunner, Skill, Tool
from ..spec import (
    AssembleContext,
    PromptAssembly,
    PromptSection,
    ToolRestriction,
)
from .executor import ToolExecutor

logger = logging.getLogger(__name__)


class SessionToolService:
    """ToolService 适配：注册落到 PluginRunner，执行走 ToolExecutor。"""

    def __init__(self, runner: PluginRunner, tool_config: ToolConfig | None = None):
        self._runner = runner
        self._executor = ToolExecutor(tool_config)

    def register_tool(self, *tools: Tool) -> None:
        self._runner.register_tool(*tools)

    def add(self, *tools: Tool) -> None:
        self.register_tool(*tools)

    def remove(self, name: str) -> None:
        self._runner.remove_tool(name)

    def remove_tool(self, name: str) -> None:
        self.remove(name)

    def has(self, name: str) -> bool:
        return any(tool.name == name for tool in self._runner.all_tools())

    def all_tools(self) -> list[Tool]:
        return self._runner.all_tools()

    def restrict(self, restriction: ToolRestriction) -> Callable[[], None]:
        """工具限制由 Agent.tool_filter 在每轮组装，适配层不叠加。"""
        return lambda: None

    def schemas(
        self,
        *,
        restriction: ToolRestriction | None = None,
        tools: dict[str, Tool] | None = None,
    ) -> list[dict[str, Any]]:
        source = {t.name: t for t in self._runner.all_tools()}
        if tools:
            source.update(tools)
        return [tool.to_schema() for tool in source.values()]

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        tool_call_id: str = "",
        *,
        restriction: ToolRestriction | None = None,
        tools: dict[str, Tool] | None = None,
    ) -> Any:
        source = {t.name: t for t in self._runner.all_tools()}
        if tools:
            source.update(tools)
        _, result = await self._executor.execute_tool(
            name, arguments or {}, tool_call_id, source
        )
        return result

    async def launch_pending(self) -> None:
        """后台作业由现有 Runner/Executor 拉起，这里保持幂等空操作。"""
        return None


class SessionPromptService:
    """PromptService 适配：会话级 section 表，交给 Runner 组装 system 提示。"""

    def __init__(self) -> None:
        self._sections: dict[str, PromptSection] = {}

    def section(self, section: PromptSection) -> Callable[[], None]:
        if section.name in self._sections:
            raise ValueError(f"prompt 段重名: {section.name}")
        self._sections[section.name] = section

        def _off() -> None:
            self._sections.pop(section.name, None)

        return _off

    async def assemble(self, context: AssembleContext) -> PromptAssembly:
        sections: list[str] = []
        for section in sorted(
            self._sections.values(), key=lambda item: (item.order, item.name)
        ):
            text = section.text(context) if callable(section.text) else section.text
            if text:
                sections.append(text)
        return PromptAssembly(sections=sections)


class SessionSkillService:
    """SkillService 适配：注册落到 PluginRunner 的技能表。"""

    def __init__(self, runner: PluginRunner) -> None:
        self._runner = runner

    def add(self, name: str, description: str, path: str) -> None:
        self._runner.register_skill(name, description, path)

    def remove(self, name: str) -> None:
        self._runner.remove_skill(name)

    def list(
        self,
        *,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> list[Skill]:
        from ..tools.tool import matches

        return [
            skill
            for name, skill in self._runner.skills.items()
            if matches(name, include, exclude)
        ]
