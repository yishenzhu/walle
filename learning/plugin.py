"""学习插件清单：agent 扩展工厂 + 可选 Web 路由安装器。"""

from ..infra import ExtensionSpec
from .extension import learning_extension


def install_web(app) -> None:
    """把学习扩展的课程路由挂到宿主 Web 应用。"""
    from .web import install

    install(app)


PLUGIN = ExtensionSpec(
    name="learning",
    factory=learning_extension,
    web=install_web,
)
