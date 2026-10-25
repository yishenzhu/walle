"""Web 插件发现：扫描包内 plugin.py 的 WEB_PLUGINS 声明（惰性导入）。"""

from __future__ import annotations

import importlib
import pkgutil

from ...plugin import Plugin


def discover_web_plugins(package: str = "walle") -> list[Plugin]:
    """扫描包内 plugin.py 的 WEB_PLUGINS，返回 Web 插件声明。"""
    base = importlib.import_module(package)
    found: list[Plugin] = []
    for info in sorted(pkgutil.iter_modules(base.__path__), key=lambda i: i.name):
        if not info.ispkg:
            continue
        manifest = f"{package}.{info.name}.plugin"
        try:
            module = importlib.import_module(manifest)
        except ModuleNotFoundError:
            continue
        for ref in getattr(module, "WEB_PLUGINS", ()):
            name, _, attr = ref.partition(":")
            plugin = getattr(importlib.import_module(name), attr, None)
            if isinstance(plugin, Plugin):
                found.append(plugin)
    return found
