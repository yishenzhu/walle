"""插件运行时：Plugin 声明 + Context（作用域）+ PluginHost（挂载编排）。"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from types import ModuleType
from typing import Any

from .events import Event, EventBus, Handler

logger = logging.getLogger(__name__)

# 当前正在 apply 的插件作用域：会话级服务据此登记注册归属，卸载时回收。
current_scope: ContextVar = ContextVar("plugin_scope", default=None)


class ServiceError(LookupError):
    """按能力协议取服务失败。"""


class ScopedLayers:
    """一个注册表的分层存储：全局层 + 按需创建的作用域覆盖层。"""

    def __init__(self) -> None:
        self.global_layer: dict[type, Any] = {}
        self.scoped: dict[object, dict[type, Any]] = {}  # 作用域 key → 覆盖层

    def layer_for(self, key: object | None) -> dict[type, Any]:
        """取写入层：key=None 即全局层，否则按需建该 key 的覆盖层。"""
        if key is None:
            return self.global_layer
        return self.scoped.setdefault(key, {})

    def resolve(self, service: type, key: object | None) -> Any | None:
        """解析实现：先查作用域覆盖层，再落全局（作用域掩盖全局）。"""
        if key is not None:
            layer = self.scoped.get(key)
            if layer is not None and service in layer:
                return layer[service]
        return self.global_layer.get(service)

    def reclaim(self, key: object | None) -> None:
        """层空即回收（对齐 harness 的 whole-layer emptiness）。"""
        if key is None:
            return
        layer = self.scoped.get(key)
        if layer is not None and not layer:
            del self.scoped[key]


class Context:
    """插件作用域：能力按层解析（作用域掩盖全局），dispose 逆序回滚。"""

    def __init__(
        self,
        parent: Context | None = None,
        *,
        host: PluginHost | None = None,
        key: object | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._parent = parent
        self._key = key  # 作用域层 key（None = 全局层）
        # 根作用域可注入总线；子作用域共享父总线。
        self._bus = (bus or EventBus()) if parent is None else parent._bus
        self._layers = ScopedLayers() if parent is None else parent._layers  # 分层能力表（共享）
        self._effects: list[Callable[[], Any]] = []
        self._detach: Callable[[], None] | None = None  # 从父 effect 表摘除自身（根为 None）
        self._disposed = False
        self._host = host  # 服务生命周期回调（仅根作用域有效）

    # 作用域定位
    def _root(self) -> Context:
        node: Context = self
        while node._parent is not None:
            node = node._parent
        return node

    @property
    def bus(self) -> EventBus:
        """根作用域事件总线（全体插件共享）。"""
        return self._root()._bus

    # 能力（键为能力协议类；写入本作用域的层）
    async def register(
        self,
        service: type,
        impl: Any,
        *,
        replace: bool = False,
    ) -> Callable[[], Awaitable[None]]:
        """按能力协议注册实现到**本作用域的层**；返回异步注销函数。"""
        self._assert_alive()
        layer = self._layers.layer_for(self._key)
        is_global = self._key is None
        if service in layer:
            if not replace:
                raise ValueError(
                    f"能力 {service!r} 已注册，禁止重复提供（替换请显式 replace=True）"
                )
            del layer[service]
            if is_global:
                await self._notify(self._root(), service, registered=False)
        layer[service] = impl
        if is_global:
            await self._notify(self._root(), service, registered=True)

        async def _unregister() -> None:
            current = self._layers.layer_for(self._key)
            if current.get(service) is impl:
                del current[service]
                self._layers.reclaim(self._key)
                if is_global:
                    await self._notify(self._root(), service, registered=False)

        self.effect(_unregister)
        return _unregister

    @staticmethod
    async def _notify(root: Context, service: type, *, registered: bool) -> None:
        """内核记账：通知观察者（Host）某能力上线/下线。"""
        if root._host is None:
            return
        if registered:
            await root._host.service_added(service)
        else:
            await root._host.service_removed(service)

    def get(self, service: type) -> Any:
        """按能力协议取实现（作用域覆盖层优先，再落全局）；未注册抛 ServiceError。"""
        impl = self._layers.resolve(service, self._key)
        if impl is None:
            raise ServiceError(f"能力 {service!r} 未注册")
        return impl

    def has(self, service: type) -> bool:
        return self._layers.resolve(service, self._key) is not None

    @property
    def services(self) -> dict[type, Any]:
        """本作用域可见的能力视图（全局 ⊕ 本作用域覆盖）。"""
        out = dict(self._layers.global_layer)
        if self._key is not None:
            out.update(self._layers.scoped.get(self._key, {}))
        return out

    # 事件面（插件唯一入口）：订阅登记 effect 自动退订；分发自 host 级总线
    def on(self, event: type, handler: Handler) -> Callable[[], None]:
        """订阅事件类（注册序即执行序）；返回退订函数，销毁自动退订。"""
        self._assert_alive()
        off = self.bus.on(event, handler)
        self.effect(off)
        return off

    async def emit(self, event: Event) -> list[Any]:
        """顺序分发：逐个 await，异常隔离；返回各监听器结果。"""
        return await self.bus.emit(event)

    async def bail(self, event: Event) -> Any | None:
        """首个表态：返回第一个非 None 结果。"""
        return await self.bus.bail(event)

    async def parallel(self, event: Event) -> list[Any]:
        """并发分发：并行 await 全部，异常隔离（进结果列表）。"""
        return await self.bus.parallel(event)

    # 生命周期
    def effect(self, cleanup: Callable[[], Any]) -> Callable[[], None]:
        """登记清理函数；返回摘除函数。dispose 时按注册逆序执行（可 async）。"""
        self._assert_alive()
        self._effects.append(cleanup)

        def _remove() -> None:
            if cleanup in self._effects:
                self._effects.remove(cleanup)

        return _remove

    def child(self) -> Context:
        """派生子作用域（插件挂载位置）；**继承本作用域的层 key**。"""
        self._assert_alive()
        child = Context(parent=self, key=self._key)
        child._detach = self.effect(child.dispose)
        return child

    def scope(self, key: object) -> Context:
        """派生**某作用域**的上下文（对齐 harness 的 per-scope 注册）。"""
        self._assert_alive()
        child = Context(parent=self, key=key)
        child._detach = self.effect(child.dispose)
        return child

    def _assert_alive(self) -> None:
        if self._disposed:
            raise RuntimeError("作用域已销毁")

    @property
    def disposed(self) -> bool:
        return self._disposed

    async def dispose(self) -> None:
        """逆序回滚自身 effect（子作用域已随 effect 登记），随后作废；幂等。"""
        if self._disposed:
            return
        self._disposed = True
        detach, self._detach = self._detach, None
        if detach is not None:
            detach()  # 从父 effect 表摘除，避免父作用域长期持有（卸载可重载）
        for cleanup in reversed(list(self._effects)):
            try:
                result = cleanup()
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001
                logger.exception("effect 清理失败")
        self._effects.clear()


# 插件入口：apply(ctx, config) 内通过 ctx 完成一切注册。
Apply = Callable[[Context, dict[str, Any]], Awaitable[None] | None]


@dataclass(frozen=True)
class Plugin:
    """一次插件声明：name / apply(ctx, config) / inject / provides / version / config。"""

    name: str
    apply: Apply
    inject: tuple[type, ...] = ()
    provides: tuple[type, ...] = ()
    version: str = "0.1.0"
    config: dict[str, Any] = field(default_factory=dict)

    def merge_config(self, override: dict[str, Any] | None) -> dict[str, Any]:
        """默认配置与用户配置浅合并（用户键优先）。"""
        merged = dict(self.config)
        merged.update(override or {})
        return merged

    @classmethod
    def from_module(cls, module: ModuleType) -> Plugin:
        """按模块约定装载：读 name/apply/inject/provides/version/config 属性。"""
        attrs = vars(module)
        apply = attrs.get("apply")
        if not callable(apply):
            raise ValueError(f"插件模块 {module.__name__} 需定义 apply(ctx, config)")
        return cls(
            name=attrs.get("name") or module.__name__,
            apply=apply,
            inject=tuple(attrs.get("inject") or ()),
            provides=tuple(attrs.get("provides") or ()),
            version=attrs.get("version") or "0.1.0",
            config=dict(attrs.get("config") or {}),
        )


def plugin(
    name: str,
    *,
    inject: tuple[type, ...] = (),
    provides: tuple[type, ...] = (),
    version: str = "0.1.0",
    config: dict[str, Any] | None = None,
) -> Callable[[Apply], Plugin]:
    """装饰器：把 async def apply(ctx, config) 包成 Plugin 声明。"""

    def deco(fn: Apply) -> Plugin:
        return Plugin(
            name=name,
            apply=fn,
            inject=tuple(inject),
            provides=tuple(provides),
            version=version,
            config=dict(config or {}),
        )

    return deco


class PluginState(StrEnum):
    PENDING = "pending"  # 依赖未就绪（等服务注册后自动激活/重载）
    LOADING = "loading"  # apply 执行中
    ACTIVE = "active"  # 依赖齐全，已挂载
    FAILED = "failed"  # apply 抛错（不再重试），已回滚注册项
    DISPOSED = "disposed"  # 已随 stop() 终局卸载


@dataclass
class PluginEntry:
    """一次插件挂载的条目：独立子作用域 + 生命周期状态。"""

    plugin: Plugin
    ctx: Context | None = None  # 激活期新建；卸载后置 None 待重载
    state: PluginState = PluginState.PENDING
    error: str | None = None
    config: dict[str, Any] = field(default_factory=dict)


class PluginHost:
    """插件运行时根：持根作用域，按 能力→依赖方 映射做依赖驱动编排。"""

    def __init__(self, bus: EventBus | None = None) -> None:
        self._root = Context(host=self, bus=bus)
        self._entries: dict[str, PluginEntry] = {}
        self._order: list[str] = []
        self._dependents: dict[type, list[str]] = {}  # 能力 → 依赖方（声明序）
        self._providers: dict[type, str] = {}  # 能力 → 提供者（重复校验用）

    @property
    def root(self) -> Context:
        """根作用域：装配方向此注入种子服务（await root.register）。"""
        return self._root

    @property
    def entries(self) -> list[PluginEntry]:
        return [self._entries[name] for name in self._order]

    # 启动
    async def launch(
        self,
        plugins: Sequence[Plugin | ModuleType],
        configs: dict[str, dict[str, Any]] | None = None,
        *,
        strict: bool = False,
    ) -> list[PluginEntry]:
        """收纳全部插件声明（PENDING）并激活依赖已就绪的；声明顺序无关。"""
        configs = configs or {}
        defs = [e if isinstance(e, Plugin) else Plugin.from_module(e) for e in plugins]
        names = [e.name for e in defs]
        dup = sorted(n for n in set(names) if names.count(n) > 1)
        if dup:
            raise ValueError(f"插件名重复: {dup}")
        claimed: dict[type, str] = {}
        for e in defs:
            for service in e.provides:
                if service in claimed:
                    raise ValueError(
                        f"能力 {service!r} 由 {claimed[service]} 与 {e.name} 重复提供"
                    )
                claimed[service] = e.name
        if strict:
            missing = {
                e.name: [c for c in e.inject if not self._root.has(c) and c not in claimed]
                for e in defs
            }
            if bad := {n: m for n, m in missing.items() if m}:
                raise LookupError(f"无提供者的依赖: {bad}")

        self._providers = claimed
        self._dependents = {}
        for e in defs:  # 声明序：同一能力的依赖方按声明先后激活（同名后到覆盖先到）
            for dep in e.inject:
                if e.name not in self._dependents.setdefault(dep, []):
                    self._dependents[dep].append(e.name)
        self._order = names
        self._entries = {
            e.name: PluginEntry(
                plugin=e,
                ctx=self._root.child(),
                config=e.merge_config(configs.get(e.name)),
            )
            for e in defs
        }
        for handle in self._entries.values():
            await self.try_activate(handle)
        return self.entries

    # 内核记账：能力上线/下线（由 Context 直接回调，非事件总线）
    async def service_added(self, service: type) -> None:
        """能力注册 → 相关 PENDING 插件尝试转激活（重载路径同样走这里）。"""
        for name in self._dependents.get(service, ()):
            handle = self._entries.get(name)
            if handle is not None and handle.state is PluginState.PENDING:
                await self.try_activate(handle)

    async def service_removed(self, service: type) -> None:
        """能力注销 → 卸载依赖它的 ACTIVE 插件，回 PENDING 等重载。"""
        for name in self._dependents.get(service, ()):
            handle = self._entries.get(name)
            if handle is not None and handle.state is PluginState.ACTIVE:
                await self.deactivate(handle)

    async def try_activate(self, handle: PluginEntry) -> None:
        """依赖齐全则挂载；否则保持 PENDING 等下一次能力注册。"""
        if handle.state is not PluginState.PENDING:
            return
        if handle.ctx is None:
            handle.ctx = self._root.child()
        if all(handle.ctx.has(dep) for dep in handle.plugin.inject):
            await self.activate(handle)

    async def activate(self, handle: PluginEntry) -> None:
        """LOADING → apply → ACTIVE；抛错 → FAILED 并回滚。"""
        if handle.state is not PluginState.PENDING:
            return
        if handle.ctx is None:
            handle.ctx = self._root.child()
        handle.state = PluginState.LOADING
        handle.error = None
        token = current_scope.set(handle.ctx)
        try:
            result = handle.plugin.apply(handle.ctx, handle.config)
            if inspect.isawaitable(result):
                await result
            handle.state = PluginState.ACTIVE
            logger.info("插件 %s ACTIVE", handle.plugin.name)
        except Exception as exc:  # noqa: BLE001 - 单插件失败隔离
            handle.state = PluginState.FAILED
            handle.error = str(exc)
            logger.exception("插件 %s 加载失败", handle.plugin.name)
            if handle.ctx is not None:
                await handle.ctx.dispose()
        finally:
            current_scope.reset(token)

    async def deactivate(self, handle: PluginEntry) -> None:
        """依赖丢失：回滚 effect、ctx 置 None，回 PENDING 等重载。"""
        if handle.state is not PluginState.ACTIVE:
            return
        try:
            if handle.ctx is not None:
                await handle.ctx.dispose()
        except Exception:  # noqa: BLE001
            logger.exception("插件 %s 卸载失败", handle.plugin.name)
        handle.ctx = None
        handle.state = PluginState.PENDING
        logger.info("插件 %s 依赖消失，回 PENDING 待重载", handle.plugin.name)

    # 停用
    async def stop(self) -> None:
        """逆序卸载全部条目（effect 逆序回滚）；本 host 可再 launch。"""
        for name in reversed(list(self._order)):
            handle = self._entries.pop(name, None)
            if handle is None:
                continue
            if handle.state in (
                PluginState.ACTIVE,
                PluginState.LOADING,
                PluginState.PENDING,
            ):
                try:
                    if handle.ctx is not None:
                        await handle.ctx.dispose()
                except Exception:  # noqa: BLE001
                    logger.exception("插件 %s 卸载失败", name)
            handle.state = PluginState.DISPOSED
        self._order.clear()
        self._dependents.clear()
        self._providers.clear()

    async def dispose(self) -> None:
        """stop() 并销毁根作用域（关停整个运行时）。"""
        await self.stop()
        await self._root.dispose()
