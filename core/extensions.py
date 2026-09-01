"""扩展系统：以 factory 函数注入能力（注册工具 / 订阅生命周期事件）。

两阶段加载（贴近 pi）：
  1. load() —— 逐个调用 factory(api)，把 on / register_tool 写入各自的
     pending Extension（纯收集，不触碰 bus / registry）。
  2. activate() —— 把成功加载的 Extension 提交：handlers 挂到 bus、tools
     注册到 registry；失败的（factory 抛异常或激活冲突）整体 discard，
     不会部分生效。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from .diagnostics import (
    DiagnosticType,
    ResourceDiagnostic,
)
from ..infra import Event, EventBus, Handler
from ..tools import Tool, ToolRegistry


class ExtensionState(StrEnum):
    LOADING = "loading"  # factory 执行完毕，待激活
    ACTIVE = "active"  # 已提交到 bus / registry
    FAILED = "failed"  # factory 抛异常或激活冲突，已丢弃


@dataclass
class Extension:
    """一次扩展加载结果：收集的 handlers / tools + 生命周期状态。"""

    name: str
    handlers: dict[Event, list[Handler]] = field(default_factory=dict)
    tools: list[Tool] = field(default_factory=list)
    state: ExtensionState = ExtensionState.LOADING
    error: str | None = None


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
        self._factories: list[tuple[str, ExtensionFactory]] = []
        self._extensions: list[Extension] = []

    def add(self, name: str, factory: ExtensionFactory) -> None:
        """注册一个扩展 factory（供 load() 执行）。"""
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

        冲突即抛错：工具重名时 registry.add_tool 整批 raise，扩展标记 FAILED，
        handlers 未挂载（不部分生效），错误经由 diagnostics 可见。不静默
        跳过、不降级——错误宁可暴露，不做 first-wins 仲裁。
        """
        for ext in self._extensions:
            if ext.state is not ExtensionState.LOADING:
                continue
            try:
                # 先注册 tools（add_tool 对重名整批抛错），失败即整体丢弃，
                # 此时 handlers 未挂载，不会部分生效。
                if ext.tools:
                    self._registry.add_tool(*ext.tools)
                for event, handlers in ext.handlers.items():
                    for handler in handlers:
                        self._bus.on(event, handler)
            except Exception as exc:  # noqa: BLE001 - 激活冲突整体丢弃
                ext.state = ExtensionState.FAILED
                ext.error = str(exc)
            else:
                ext.state = ExtensionState.ACTIVE

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
