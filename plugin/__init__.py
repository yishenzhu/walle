"""插件架构：事件总线 + Plugin（声明）+ 运行时（Context / PluginHost）。"""

from .discovery import discover, discover_packages
from .events import EventBus, Handler
from .runtime import (
    Context,
    Plugin,
    PluginEntry,
    PluginHost,
    PluginState,
    ServiceError,
    on_dispose,
    plugin,
)

__all__ = [
    "EventBus",
    "Handler",
    "Plugin",
    "plugin",
    "discover",
    "discover_packages",
    "Context",
    "ServiceError",
    "on_dispose",
    "PluginHost",
    "PluginEntry",
    "PluginState",
]