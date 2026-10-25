"""插件发现：扫描目录，把模块约定（apply/inject/provides）装载为 Plugin 声明。"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil
from dataclasses import replace
from pathlib import Path

from .runtime import Plugin

logger = logging.getLogger(__name__)


def discover(
    root: str | Path,
    *,
    enabled: list[str] | None = None,
    disabled: list[str] | None = None,
) -> list[Plugin]:
    """扫描插件目录，返回插件声明列表（失败项以必失败声明占位）。"""
    base = Path(root)
    if not base.is_dir():
        return []
    enabled, disabled = enabled or [], disabled or []
    out: list[Plugin] = []
    for entry in sorted(base.iterdir()):  # 目录名排序 = 加载顺序
        if not entry.is_dir():
            continue
        name = entry.name
        if (enabled and name not in enabled) or name in disabled:
            continue
        found = load(name, entry)
        if found is not None:
            out.append(found)
    return out


def discover_packages(package: str = "walle") -> list[Plugin]:
    """扫描包内 <subpackage>/plugin.py 的 PLUGIN 声明（自动加载领域插件）。"""
    base = importlib.import_module(package)
    found: list[Plugin] = []
    for info in sorted(pkgutil.iter_modules(base.__path__), key=lambda i: i.name):
        if not info.ispkg:
            continue
        plugin_name = f"{package}.{info.name}.plugin"
        try:
            if importlib.util.find_spec(plugin_name) is None:
                continue
        except ModuleNotFoundError:
            continue
        module = importlib.import_module(plugin_name)
        plugin = getattr(module, "PLUGIN", None)
        if isinstance(plugin, Plugin):
            found.append(plugin)
    return found


def load(name: str, entry: Path) -> Plugin | None:
    """装载单个插件目录（缺入口 / 无 apply / 导入错误 → 必失败声明）。"""
    mod_path = entry / "plugin.py"
    try:
        if not mod_path.is_file():
            raise FileNotFoundError(f"缺入口文件: {mod_path}")
        spec = importlib.util.spec_from_file_location(f"ext_{name}", mod_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载: {mod_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ext = Plugin.from_module(module)
        return ext if getattr(module, "name", None) else replace(ext, name=name)
    except Exception as exc:  # noqa: BLE001 - 单项失败隔离，留给 PluginHost 标记 FAILED
        logger.warning("插件 %s 装载失败: %s", name, exc)
        # 绑到普通局部：`except ... as exc` 会在此块结束时 del exc，闭包会捕到空 cell
        error = exc

        async def fail(ctx, config) -> None:
            raise error

        return Plugin(name=name, apply=fail)
