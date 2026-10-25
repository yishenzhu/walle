"""Web 宿主能力面：插件向宿主挂载路由/静态资源。

插件只依赖本协议，不 import FastAPI 或具体 Web 通道；宿主在 Web 进程中
注册实现（FastAPI app / shell / static_dir），插件经 ctx.get(WebService) 使用。
"""

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class WebService(Protocol):
    """Web 宿主要能力：返回壳页面、静态目录，并挂载插件路由。"""

    @property
    def shell(self) -> Any:
        """宿主 SPA 壳（通常为 async 返回 HTMLResponse 的可调用对象）。"""

    @property
    def static_dir(self) -> Path:
        """宿主静态资源目录（供插件导出/打包复用）。"""

    def include_router(self, router: Any) -> None:
        """把插件提供的 APIRouter 挂到宿主应用。"""

    def mount_static(self, prefix: str, directory: Path) -> None:
        """把插件静态目录挂到宿主 URL 前缀。"""

    def register_ui(self, manifest: dict[str, Any]) -> None:
        """登记插件 UI 清单（导航、脚本、样式），由 Shell 动态加载。"""
