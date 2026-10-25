"""插件架构：事件总线 + Plugin（声明）+ 运行时（Context / PluginHost）。

EventBus：事件类订阅、实例分发，MRO 命中基类订阅（域订阅/全局兜底），5 种分发。
Plugin：apply(ctx, config) 入口，inject/provides 声明依赖与提供的服务。
runtime：Plugin 声明 + Context（能力注册 / 事件 / effect）+ PluginHost。
discovery：扫描插件目录（模块约定）产出 Plugin 声明。
"""

from .discovery import discover, discover_packages
from .events import Event, EventBus, Handler, Stop
from .runtime import (
    Context,
    Plugin,
    PluginEntry,
    PluginHost,
    PluginState,
    ServiceError,
    plugin,
)

__all__ = [
    "Event",
    "EventBus",
    "Handler",
    "Stop",
    "Plugin",
    "plugin",
    "discover",
    "discover_packages",
    "Context",
    "ServiceError",
    "PluginHost",
    "PluginEntry",
    "PluginState",
]