"""扩展系统：以 factory 函数注入能力（注册工具 / 订阅生命周期事件）。

两阶段加载（贴近 pi）：
  1. load() —— 逐个调用 factory(api)，把 on / register_tool 写入各自的
     pending Extension（纯收集，不触碰 bus / registry）。
  2. activate() —— 把成功加载的 Extension 提交：handlers 挂到 bus、tools
     注册到 registry；失败的（factory 抛异常或激活冲突）整体 discard，
     不会部分生效。
"""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .diagnostics import (
    DiagnosticType,
    ResourceDiagnostic,
)
from ..conf import auto_path
from ..infra import Event, EventBus, Handler
from ..tools import Tool, ToolRegistry

logger = logging.getLogger(__name__)


class ExtensionState(StrEnum):
    LOADING = "loading"  # factory 执行完毕，待激活
    ACTIVE = "active"  # 已提交到 bus / registry
    FAILED = "failed"  # factory 抛异常或激活失败，已丢弃
    UNLOADED = "unloaded"  # 已卸载（reload 前或手动卸载）


@dataclass
class Extension:
    """一次扩展加载结果：收集的 handlers / tools + 生命周期状态。"""

    name: str
    handlers: dict[Event, list[Handler]] = field(default_factory=dict)
    tools: list[Tool] = field(default_factory=list)
    commands: dict[str, Command] = field(default_factory=dict)
    state: ExtensionState = ExtensionState.LOADING
    error: str | None = None
    # 激活成功后记录实际挂载，供 unload 精确摘除
    mounted_tools: list[str] = field(default_factory=list)
    mounted_handlers: list[tuple[Event, Handler]] = field(default_factory=list)


@dataclass(frozen=True)
class Command:
    """斜杠命令：用户输入 /<name> [args] 时不经 agent，直达处理器。"""

    name: str
    description: str
    handler: Callable[[str], Awaitable[str]]


class ExtensionAPI:
    """扩展 factory 拿到的注册接口：load 期收集，activate 期落盘。

    所有方法只写 pending Extension，杜绝加载期就污染 bus / registry——
    一个扩展失败不会泄漏半套注册。
    """

    def __init__(self, ext: Extension) -> None:
        self._ext = ext

    def on(self, event: Event, handler: Handler) -> None:
        """订阅生命周期事件（activate 时统一挂到 bus）。"""
        self._ext.handlers.setdefault(event, []).append(handler)

    def register_tool(self, tool: Tool) -> None:
        """注册一个工具（activate 时统一写入 registry）。"""
        self._ext.tools.append(tool)

    def register_command(self, name: str, description: str, handler) -> None:
        """注册斜杠命令 /<name>（handler 收 args 字符串，返回回复文本）。"""
        self._ext.commands[name] = Command(name, description, handler)


ExtensionFactory = Callable[[ExtensionAPI], Awaitable[None]]


class ExtensionRegistry:
    """进程级扩展容器：load() 收集，activate() 提交，失败整体丢弃。"""

    def __init__(
        self,
        bus: EventBus,
        registry: ToolRegistry,
    ) -> None:
        self._bus = bus
        self._registry = registry
        self._commands: dict[str, Command] = {}
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

    async def activate(self) -> None:
        """第二阶段：把成功加载的 Extension 提交到 bus / registry。

        工具同名 = 后到者覆盖（registry 语义），不视为冲突；真正失败只剩
        factory 抛异常（load 阶段已标 FAILED）。挂载成功记录到 Extension，
        供 unload/reload 精确摘除。
        """
        for ext in self._extensions:
            if ext.state is ExtensionState.LOADING:
                await self._activate_one(ext)

    async def _activate_one(self, ext: Extension) -> None:
        """激活单个扩展：注册工具 + 挂 handlers + 挂命令，成功记录挂载。"""
        try:
            if ext.tools:
                self._registry.add_tool(*ext.tools)
                ext.mounted_tools = [t.name for t in ext.tools]
            for event, handlers in ext.handlers.items():
                for handler in handlers:
                    self._bus.on(event, handler)
                    ext.mounted_handlers.append((event, handler))
            for name, cmd in ext.commands.items():
                self._commands[name] = cmd
        except Exception as exc:  # noqa: BLE001 - 激活失败整体丢弃
            ext.state = ExtensionState.FAILED
            ext.error = str(exc)
        else:
            ext.state = ExtensionState.ACTIVE

    def unload(self, name: str) -> None:
        """卸载扩展：摘除其挂载的工具 / 事件监听器 / 命令（幂等）。

        摘除前检查当前持有者是否仍是本扩展：工具按实例比对、命令按对象
        比对，避免误删后到覆盖者的注册。
        """
        for ext in self._extensions:
            if ext.name != name or ext.state is not ExtensionState.ACTIVE:
                continue
            for event, handler in ext.mounted_handlers:
                self._bus.off(event, handler)
            ext.mounted_handlers.clear()
            current = {t.name: t for t in self._registry.all_tools()}
            for tool in ext.tools:
                if current.get(tool.name) is tool:  # 仍是我的实例才摘
                    self._registry.remove_tool(tool.name)
            ext.mounted_tools.clear()
            for cmd_name, cmd in ext.commands.items():
                if self._commands.get(cmd_name) is cmd:  # 未被后到者覆盖才摘
                    del self._commands[cmd_name]
            ext.state = ExtensionState.UNLOADED
            logger.info(f"extension unloaded: {name}")
            return
        logger.warning(f"unload skipped (not active): {name}")

    async def reload(self, name: str) -> None:
        """重载扩展：卸载后重跑 factory 再激活（替换旧扩展记录）。"""
        self.unload(name)
        factory = next((f for n, f in self._factories if n == name), None)
        if factory is None:
            raise KeyError(f"no such extension: {name}")

        ext = Extension(name=name)
        try:
            await factory(ExtensionAPI(ext))
        except Exception as exc:  # noqa: BLE001 - 重载失败标 FAILED
            ext.state = ExtensionState.FAILED
            ext.error = str(exc)
        self._replace(ext)
        await self._activate_one(ext)

    def _replace(self, ext: Extension) -> None:
        """用重载出的新 Extension 顶替同名旧记录（找不到则追加）。"""
        for i, e in enumerate(self._extensions):
            if e.name == ext.name:
                self._extensions[i] = ext
                return
        self._extensions.append(ext)

    async def dispatch(self, text: str) -> str | None:
        """分发斜杠命令：命中 /<name> [args] 返回回复；未命中返回 None（回退 agent）。"""
        if not text.startswith("/"):
            return None
        cmd_name, _, args = text[1:].partition(" ")
        cmd = self._commands.get(cmd_name)
        if cmd is None:
            return None
        return await cmd.handler(args.strip())

    @property
    def commands(self) -> dict[str, Command]:
        """全部已激活命令（name → Command）。"""
        return dict(self._commands)

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

    @property
    def active(self) -> list[Extension]:
        """已成功激活的扩展。"""
        return [e for e in self._extensions if e.state is ExtensionState.ACTIVE]
