"""扩展系统：加载器(ExtensionRegistry)产出声明，激活器(ExtensionRunner)按会话生效。

- ExtensionRegistry（进程级一次）：add/discover 收集 factory，load() 产出
  Extension 声明（tools/handlers/commands）。无副作用、不持有 bus/工具表。
- ExtensionRunner（每会话一个）：把选中的扩展激活到本会话的 bus + 工具表 +
  命令表；支持 unload/重激活。同一扩展可被多个会话独立激活。
"""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .diagnostics import (
    DiagnosticType,
    ResourceDiagnostic,
)
from ..conf import auto_path
from ..channel import Channel
from .event_bus import EventBus, Handler
from .tool import Tool

logger = logging.getLogger(__name__)


class ExtensionState(StrEnum):
    LOADING = "loading"  # factory 执行完毕，声明可用
    FAILED = "failed"  # factory 抛异常，声明不可用


@dataclass
class Extension:
    """一次扩展加载的声明：收集的 handlers / tools / skills / commands + 状态。"""

    name: str
    handlers: dict[type, list[Handler]] = field(default_factory=dict)
    tools: list[Tool] = field(default_factory=list)
    skills: dict[str, Skill] = field(default_factory=dict)
    commands: dict[str, Command] = field(default_factory=dict)
    state: ExtensionState = ExtensionState.LOADING
    error: str | None = None


@dataclass(frozen=True)
class Skill:
    """技能声明：渐进式披露——name/description 常驻提示词，path 供 read 加载全文。"""

    name: str
    description: str
    path: str  # SKILL.md 完整路径


@dataclass
class CommandContext:
    """命令执行上下文：暴露会话底层能力，用法由命令自己决定。

    channel = 会话 transport：notify(Delta/DeltaEnd...) 推送、call(Inquiry)
    向用户提问；bus = 会话事件总线（消息输出等事件）。命令按需自取，
    不预设 UI 语义。
    """

    channel: Channel | None
    bus: EventBus


@dataclass(frozen=True)
class Command:
    """斜杠命令：用户输入 /<name> [args] 时不经 agent，直达处理器。

    handler(args, ctx)：推送与交互由命令经 ctx（notify/confirm/select/
    input）自己决定；不调用即静默。
    """

    name: str
    description: str
    handler: Callable[[str, CommandContext | None], Awaitable[None]]


class ExtensionAPI:
    """扩展 factory 拿到的注册接口：load 期收集，activate 期落盘。

    所有方法只写 pending Extension，杜绝加载期就污染 bus / registry——
    一个扩展失败不会泄漏半套注册。
    """

    def __init__(self, ext: Extension) -> None:
        self._ext = ext

    def on(self, event_type: type, handler: Handler) -> None:
        """订阅一个事件类（activate 时统一挂到 bus）。"""
        self._ext.handlers.setdefault(event_type, []).append(handler)

    def register_tool(self, tool: Tool) -> None:
        """注册一个工具（activate 时统一写入 registry）。"""
        self._ext.tools.append(tool)

    def register_command(self, name: str, description: str, handler) -> None:
        """注册斜杠命令 /<name>（handler 收 args 字符串，返回回复文本）。"""
        self._ext.commands[name] = Command(name, description, handler)

    def register_skill(self, name: str, description: str, path: str) -> None:
        """注册一个技能声明（name/description 进提示词，path 供 read 加载全文）。"""
        self._ext.skills[name] = Skill(name, description, path)


ExtensionFactory = Callable[[ExtensionAPI], Awaitable[None]]


class ExtensionRegistry:
    """进程级扩展加载器：add/discover 收集 factory，load() 产出扩展声明。

    只负责"把扩展代码加载成声明"(Extension: tools/handlers/commands)。
    激活到某会话(挂 bus/工具表/命令)由会话级 ExtensionRunner 承担——
    本类不持有 bus / 工具表，无任何副作用。
    """

    def __init__(self) -> None:
        self._factories: list[tuple[str, ExtensionFactory]] = []
        self._extensions: list[Extension] = []

    def add(self, name: str, factory: ExtensionFactory) -> None:
        """注册一个扩展 factory（供 load() 执行）。"""
        self._factories.append((name, factory))

    def discover(
        self,
        root: str | Path = ".agent/extensions",
        enabled: list[str] | None = None,
        disabled: list[str] | None = None,
    ) -> None:
        """扫描扩展目录，按启停名单过滤后注册 factory。

        每个子目录 = 一个扩展，入口为 <dir>/extension.py（顶层
        load_extension(api) 或 main(api)）。import / 入口缺失等错误
        留到 load() 阶段暴露为 FAILED（失败隔离），此处只收集。
        """
        enabled = enabled or []
        disabled = disabled or []
        base = Path(auto_path(str(root)))  # 相对路径按项目根解析
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

            async def factory(api: ExtensionAPI, entry: Path = entry) -> None:
                mod_path = entry / "extension.py"
                if not mod_path.is_file():
                    raise FileNotFoundError(f"缺入口文件: {mod_path}")
                spec = importlib.util.spec_from_file_location(
                    f"ext_{entry.name}", mod_path
                )
                if spec is None or spec.loader is None:
                    raise ImportError(f"无法加载: {mod_path}")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                fn = getattr(module, "load_extension", None) or getattr(
                    module, "main", None
                )
                if fn is None:
                    raise AttributeError(
                        "入口需定义 async load_extension(api) 或 main(api)"
                    )
                result = fn(api)
                if hasattr(result, "__await__"):
                    await result

            self._factories.append((name, factory))

    async def load(self) -> None:
        """第一阶段：运行全部 factory，收集到各自的 pending Extension。

        factory 抛异常 → 该扩展标记 FAILED（不激活），继续加载其余。
        """
        self._extensions.clear()
        for name, factory in self._factories:
            ext = Extension(name=name)
            try:
                await factory(ExtensionAPI(ext))
            except Exception as exc:  # noqa: BLE001 - 单个扩展失败不阻断
                ext.state = ExtensionState.FAILED
                ext.error = str(exc)
            self._extensions.append(ext)

    @property
    def diagnostics(self) -> list[ResourceDiagnostic]:
        """全部扩展的诊断（加载/激活失败）。"""
        out: list[ResourceDiagnostic] = []
        for ext in self._extensions:
            if ext.state is ExtensionState.FAILED and ext.error:
                out.append(
                    ResourceDiagnostic(
                        type=DiagnosticType.ERROR,
                        message=ext.error,
                        path=ext.name,
                    )
                )
        return out

    @property
    def extensions(self) -> list[Extension]:
        """全部扩展（按加载顺序）。"""
        return self._extensions


class ExtensionRunner:
    """会话级扩展激活层：每会话绑定一份。

    一个会话一个实例：持有本会话的 bus、工具表、技能表、命令表——扩展
    激活的 tools/skills/handlers/commands 全挂到这里。工具表即会话工具源：
    Session 每轮从这里取 all_tools 交给 Agent 过滤。激活记录 per-session
    保存（同一扩展可被多个会话激活，不污染 Extension 声明）。
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._tools: list[Tool] = []  # 会话工具表（扩展激活落点）
        self._skills: dict[str, Skill] = {}  # 会话技能表（渐进式披露清单）
        self._commands: dict[str, Command] = {}
        self._mounts: dict[str, ExtensionMount] = {}  # 扩展名 → 本会话挂载

    # ── 工具表（add/remove/query，替代 ToolRegistry）──────
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
        """按名摘除工具（扩展卸载时用）。不存在则忽略。"""
        self._tools = [t for t in self._tools if t.name != name]

    def all_tools(self) -> list[Tool]:
        """本会话全部工具（Agent 工具源）。"""
        return list(self._tools)

    def activate(self, *extensions: Extension) -> None:
        """把选中的扩展挂载到本会话（工具同名后到覆盖；事件挂本会话 bus）。"""
        for ext in extensions:
            if ext.name in self._mounts:
                self.unload(ext.name)  # 重新激活前先摘旧的
            mount = ExtensionMount()
            try:
                if ext.tools:
                    self.register_tool(*ext.tools)
                    mount.tools = list(ext.tools)
                for name, skill in ext.skills.items():
                    self._skills[name] = skill
                    mount.skills.append(name)
                for event_type, handlers in ext.handlers.items():
                    for handler in handlers:
                        self._bus.on(event_type, handler)
                        mount.handlers.append((event_type, handler))
                for name, cmd in ext.commands.items():
                    self._commands[name] = cmd
                    mount.commands.append(name)
            except Exception:
                self.rollback(mount)
                raise
            self._mounts[ext.name] = mount

    def unload(self, name: str) -> None:
        """摘除某扩展在本会话的挂载（工具/事件/命令）。"""
        mount = self._mounts.pop(name, None)
        if mount is None:
            return
        self.rollback(mount)

    def rollback(self, mount: ExtensionMount) -> None:
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
        for name in mount.commands:
            if name in self._commands:
                del self._commands[name]

    async def dispatch(
        self, text: str, ctx: CommandContext | None = None
    ) -> bool:
        """分发本会话斜杠命令：命中返回 True，未命中返回 False（回退 agent）。

        ctx 为命令执行上下文（会话组装：notify 推送 / 提问交互），由
        调用方注入；命令推送与否经 ctx 自决，测试可传 None 或假 ctx。
        """
        if not text.startswith("/"):
            return False
        name, _, args = text[1:].partition(" ")
        cmd = self._commands.get(name)
        if cmd is None:
            return False
        await cmd.handler(args.strip(), ctx)
        return True

    @property
    def commands(self) -> dict[str, Command]:
        return dict(self._commands)

    @property
    def skills(self) -> dict[str, Skill]:
        """本会话可用技能（name→Skill），供 system prompt 拼技能清单。"""
        return dict(self._skills)

    @property
    def active_names(self) -> set[str]:
        return set(self._mounts)


@dataclass
class ExtensionMount:
    """单个扩展在一次会话激活中的挂载记录（per-session，非扩展声明）。"""

    tools: list[Tool] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    handlers: list[tuple[type, Handler]] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HookVerdict:
    """工具执行前钩子（TOOL_EXECUTION_START）的表态。

    返回 None = 无意见放行；返回 HookVerdict 即表态（block 与 arguments
    至少一个非空，否则 ValueError）：
    block=reason → 阻止执行，reason 透传给模型；
    arguments=args → 放行并改写本次调用参数。
    改写按注册顺序应用（后者覆盖前者）。
    """

    block: str | None = None
    arguments: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.block is None and self.arguments is None:
            raise ValueError("HookVerdict 必须表态：block=reason 或 arguments=args")

    @property
    def blocks(self) -> bool:
        return self.block is not None
