"""插件运行时适配层：把 Context/Service 能力桥接到现有会话运行时。"""

from __future__ import annotations

from collections.abc import Callable
from fnmatch import fnmatch
from typing import Any

from ..conf import ToolConfig
from ..infra import PluginRunner, Skill, Tool
from ..spec import ToolRestriction
from .executor import ToolExecutor


class SessionToolService:
    """ToolService 适配：注册落到 PluginRunner，执行走 ToolExecutor。"""

    def __init__(self, runner: PluginRunner, tool_config: ToolConfig | None = None):
        self._runner = runner
        self._executor = ToolExecutor(tool_config)

    def add(self, *tools: Tool) -> None:
        self._runner.register_tool(*tools)

    def remove(self, name: str) -> None:
        self._runner.remove_tool(name)

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
        return [
            skill
            for name, skill in self._runner.skills.items()
            if _matches(name, include, exclude)
        ]


class SessionAgentService:
    """AgentService 适配：当前 agent 与按名加载。"""

    def __init__(self, session) -> None:
        self._session = session

    @property
    def current(self):
        return self._session.agent

    def get(self, name: str):
        return self._session.get_agent(name)


def _matches(name: str, include: list[str] | None, exclude: list[str] | None) -> bool:
    """glob 筛选：include 白名单（None=全收，[]=全拒）；exclude 优先。"""
    if exclude and any(fnmatch(name, p) for p in exclude):
        return False
    if include is None:
        return True
    return any(fnmatch(name, p) for p in include)
