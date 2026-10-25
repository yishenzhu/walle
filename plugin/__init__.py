"""插件架构：事件总线 + Plugin（声明）+ 运行时（Context / PluginHost）。"""

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