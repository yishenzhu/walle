import asyncio
import logging

from .channel import SessionServer
from .conf import Config
from .core import SessionManager
from .infra import OpenAIProvider, setup_logger, setup_telemetry
from .plugin import Plugin, discover, discover_packages
from .tools import MCPManager, Sandbox
from .tools.approval import native_approval
from .tools.builtin.plugin import builtin_plugin
from .tools.skill import native_skill

logger = logging.getLogger(__name__)


async def main() -> None:
    """启动会话服务端，供 Web 等通道接入。

    当前只提供 Web UI；其他通道可复用同一 SessionServer。"""
    conf = Config.load()
    setup_logger(conf.log)
    setup_telemetry(conf.telemetry)
    OpenAIProvider.load_env()
    # 进程默认模型：装配根构造一次并注入管理器（会话无自带 model 配置时沿用）
    provider = OpenAIProvider.get_default()

    # 进程级共享 MCP 客户端：连接一次，组装成"mcp"插件进插件池
    mcp = MCPManager()
    await mcp.connect()

    # 插件声明：进程级加载，会话级激活（每会话独立 host / bus / 工具表）。
    sandbox = Sandbox()
    plugins: list[Plugin] = [
        builtin_plugin,
        sandbox.plugin(),  # 同名覆盖 bash，须在 builtin 后
        mcp.plugin(),
        native_skill,
        native_approval,
        *discover_packages(),
        *discover(
            root=conf.plugin.dir,
            enabled=conf.plugin.enabled,
            disabled=conf.plugin.disabled,
        ),
    ]
    logger.info(f"plugins loaded: {len(plugins)}")

    sessions = SessionManager(
        tool_config=conf.tool,
        plugins=plugins,
        provider=provider,
        storage=conf.session.storage,
        path=conf.session.path,
    )
    server = SessionServer(sessions=sessions)
    await server.start()

    try:
        # 事件驱动，主协程挂起等待（Ctrl+C 退出）
        await asyncio.Event().wait()
    finally:
        await server.stop()
        await sessions.close()  # 停机销毁全部会话（关存储/作业）
        await mcp.close()  # 关闭进程级 MCP 客户端


if __name__ == "__main__":
    asyncio.run(main())
