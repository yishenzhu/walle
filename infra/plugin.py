"""插件系统：加载器(PluginManager)产出声明，激活器(PluginRunner)按会话生效。"""

from __future__ import annotations

import importlib.util
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..conf import resolve_path
from ..plugin.events import EventBus, Handler
from ..spec import DiagnosticType, ResourceDiagnostic
from .tool import Tool


class PluginState(StrEnum):
    READY = "ready"  # factory 执行完毕，声明可用
    FAILED = "failed"  # factory 抛异常，声明不可用


InstructionsProvider = Callable[[dict[str, Any]], str | None]


@dataclass
class PluginDeclaration:
    """一次插件加载的声明：收集的 handlers / tools / skills + 状态。"""

    name: str
    handlers: dict[type, list[Handler]] = field(default_factory=dict)
    tools: list[Tool] = field(default_factory=list)
    skills: dict[str, Skill] = field(default_factory=dict)
    instructions: list[InstructionsProvider] = field(default_factory=list)
    state: PluginState = PluginState.READY
    error: str | None = None


@dataclass(frozen=True)
class Skill:
    """技能声明：渐进式披露——name/description 常驻提示词，path 供 read 加载全文。"""

    name: str
    description: str
    path: str  # SKILL.md 完整路径


class PluginAPI:
    """插件 factory 拿到的注册接口：load 期收集，activate 期落盘。"""

    def __init__(self, plugin: PluginDeclaration) -> None:
        self._plugin = plugin

    def on(self, event_type: type, handler: Handler) -> None:
        """订阅一个事件类（activate 时统一挂到 bus）。"""
        self._plugin.handlers.setdefault(event_type, []).append(handler)

    def register_tool(self, tool: Tool) -> None:
        """注册一个工具（activate 时统一写入 manager）。"""
        self._plugin.tools.append(tool)

    def register_skill(self, name: str, description: str, path: str) -> None:
        """注册一个技能声明（name/description 进提示词，path 供 read 加载全文）。"""
        self._plugin.skills[name] = Skill(name, description, path)

    def register_instructions(self, provider: InstructionsProvider) -> None:
        """注册动态指令提供者：每次模型调用前按会话 metadata 返回文本。"""
        self._plugin.instructions.append(provider)


PluginFactory = Callable[[PluginAPI], Awaitable[None]]


class PluginManager:
    """进程级插件加载器：add/discover 收集 factory，load() 产出插件声明。"""

    def __init__(self) -> None:
        self._factories: list[tuple[str, PluginFactory]] = []
        self._plugins: list[PluginDeclaration] = []

    def add(self, name: str, factory: PluginFactory) -> None:
        """注册一个插件 factory（供 load() 执行）。"""
        self._factories.append((name, factory))

    def discover(
        self,
        root: str | Path = ".agent/plugins",
        enabled: list[str] | None = None,
        disabled: list[str] | None = None,
    ) -> None:
        """扫描插件目录，按启停名单过滤后注册 factory。"""
        enabled = enabled or []
        disabled = disabled or []
        base = Path(resolve_path(str(root)))  # 相对路径按项目根解析
        if not base.is_dir():
            return

        for entry in sorted(base.iterdir()):  # 目录名排序 = 加载顺序
            if not entry.is_dir():
                continue
            name = entry.name
            if enabled and name not in enabled:
                continue
            if name in disabled:
                continue

            async def factory(api: PluginAPI, entry: Path = entry) -> None:
                mod_path = entry / "plugin.py"
                if not mod_path.is_file():
                    raise FileNotFoundError(f"缺入口文件: {mod_path}")
                spec = importlib.util.spec_from_file_location(
                    f"plugin_{entry.name}", mod_path
                )
                if spec is None or spec.loader is None:
                    raise ImportError(f"无法加载: {mod_path}")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                fn = getattr(module, "load_plugin", None) or getattr(
                    module, "main", None
                )
                if fn is None:
                    raise AttributeError(
                        "入口需定义 async load_plugin(api) 或 main(api)"
                    )
                result = fn(api)
                if hasattr(result, "__await__"):
                    await result

            self._factories.append((name, factory))

    async def load(self) -> None:
        """第一阶段：运行全部 factory，收集到各自的 pending PluginDeclaration。"""
        self._plugins.clear()
        for name, factory in self._factories:
            plugin = PluginDeclaration(name=name)
            try:
                await factory(PluginAPI(plugin))
            except Exception as exc:  # noqa: BLE001 - 单个插件失败不阻断
                plugin.state = PluginState.FAILED
                plugin.error = str(exc)
            self._plugins.append(plugin)

    @property
    def diagnostics(self) -> list[ResourceDiagnostic]:
        """全部插件的诊断（加载/激活失败）。"""
        out: list[ResourceDiagnostic] = []
        for plugin in self._plugins:
            if plugin.state is PluginState.FAILED and plugin.error:
                out.append(
                    ResourceDiagnostic(
                        type=DiagnosticType.ERROR,
                        message=plugin.error,
                        path=plugin.name,
                    )
                )
        return out

    @property
    def plugins(self) -> list[PluginDeclaration]:
        """全部插件（按加载顺序）。"""
        return self._plugins


class PluginRunner:
    """会话级插件激活层：每会话绑定一份。"""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._tools: list[Tool] = []  # 会话工具表（插件激活落点）
        self._skills: dict[str, Skill] = {}  # 会话技能表（渐进式披露清单）
        self._instructions: list[InstructionsProvider] = []  # 动态指令提供者
        self._mounts: dict[str, PluginMount] = {}  # 插件名 → 本会话挂载

    # ── 工具表（add/remove/query，实现 ToolTable 能力面）────
    def register_tool(self, *tools: Tool) -> None:
        """注册工具：同名后到者覆盖先到者。同批重名视为编程错误。"""
        names = [t.name for t in tools]
        if len(set(names)) != len(names):
            raise ValueError(f"Duplicate tool name in batch: {names}")
        new_names = set(names)
        self._tools = [t for t in self._tools if t.name not in new_names]
        for tool in tools:
            self._tools.append(tool)

    def remove_tool(self, name: str) -> None:
        """按名摘除工具（插件卸载时用）。不存在则忽略。"""
        self._tools = [t for t in self._tools if t.name != name]

    def all_tools(self) -> list[Tool]:
        """本会话全部工具（Agent 工具源）。"""
        return list(self._tools)

    def activate(self, *plugins: PluginDeclaration) -> None:
        """把选中的插件挂载到本会话（工具同名后到覆盖；事件挂本会话 bus）。"""
        for plugin in plugins:
            if plugin.name in self._mounts:
                self.unload(plugin.name)  # 重新激活前先摘旧的
            mount = PluginMount()
            try:
                if plugin.tools:
                    self.register_tool(*plugin.tools)
                    mount.tools = list(plugin.tools)
                for name, skill in plugin.skills.items():
                    self._skills[name] = skill
                    mount.skills.append(name)
                for event_type, handlers in plugin.handlers.items():
                    for handler in handlers:
                        self._bus.on(event_type, handler)
                        mount.handlers.append((event_type, handler))
                for provider in plugin.instructions:
                    self._instructions.append(provider)
                    mount.instructions.append(provider)
            except Exception:
                self._rollback(mount)
                raise
            self._mounts[plugin.name] = mount

    def unload(self, name: str) -> None:
        """摘除某插件在本会话的挂载（工具/事件）。"""
        mount = self._mounts.pop(name, None)
        if mount is None:
            return
        self._rollback(mount)

    def _rollback(self, mount: PluginMount) -> None:
        """摘除一次激活的全部副作用（失败回滚或主动卸载共用）。"""
        current = {t.name: t for t in self._tools}
        for tool in mount.tools:
            if current.get(tool.name) is tool:  # 仍是本挂载的实例才摘
                self.remove_tool(tool.name)
        for name in mount.skills:
            if name in self._skills:  # 技能同名后到覆盖，卸载摘除即可
                del self._skills[name]
        for event_type, handler in mount.handlers:
            self._bus.off(event_type, handler)
        for provider in mount.instructions:
            if provider in self._instructions:
                self._instructions.remove(provider)

    def register_skill(self, name: str, description: str, path: str) -> Callable[[], None]:
        """注册一个技能声明（供 SkillService 适配层调用）；返回注销函数。"""
        self._skills[name] = Skill(name=name, description=description, path=path)

        def _off() -> None:
            self._skills.pop(name, None)

        return _off

    def remove_skill(self, name: str) -> None:
        self._skills.pop(name, None)

    @property
    def skills(self) -> dict[str, Skill]:
        """本会话可用技能（name→Skill），供 system prompt 拼技能清单。"""
        return dict(self._skills)

    def instructions(self, metadata: dict[str, Any]) -> list[str]:
        """按会话 metadata 收集插件动态指令（空结果跳过）。"""
        prompts = [provider(metadata) for provider in self._instructions]
        return [prompt for prompt in prompts if prompt]

    @property
    def active_plugins(self) -> set[str]:
        return set(self._mounts)


@dataclass
class PluginMount:
    """单个插件在一次会话激活中的挂载记录（per-session，非插件声明）。"""

    tools: list[Tool] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    handlers: list[tuple[type, Handler]] = field(default_factory=list)
    instructions: list[InstructionsProvider] = field(default_factory=list)


@dataclass(frozen=True)
class HookVerdict:
    """工具执行前钩子（PreExecuteToolEvent）的表态。"""

    block: str | None = None
    arguments: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.block is None and self.arguments is None:
            raise ValueError("HookVerdict 必须表态：block=reason 或 arguments=args")

    @property
    def blocks(self) -> bool:
        return self.block is not None
