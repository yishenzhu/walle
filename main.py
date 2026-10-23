import asyncio
import logging

from .channel.cli import CLIServer
from .conf import Config
from .core import (
    ExtensionManager,
    SessionManager,
)
from .infra import OpenAIProvider, setup_logger, setup_telemetry
from .tools import Approval, MCPManager, Sandbox
from .tools.builtin.extension import builtin_extension
from .tools.skill import SkillLoader

logger = logging.getLogger(__name__)


async def main() -> None:
    """启动 agent 服务端 + CLI 客户端连接通道。

    CLI 客户端需显式启动交互：python -m walle.channel.cli。
    """
    conf = Config.load()
    setup_logger(conf.log)
    setup_telemetry(conf.telemetry)
    OpenAIProvider.load_env()
    # 进程默认模型：装配根构造一次并注入管理器（会话无自带 model 配置时沿用）
    provider = OpenAIProvider.get_default()

    # 进程级共享 MCP 客户端：连接一次，组装成"mcp"扩展进扩展池
    mcp = MCPManager()
    await mcp.connect()

    # 进程级扩展加载器：内置工具扩展 + MCP 扩展 + 技能 + 审批 + 沙箱 + .agent/extensions/ 用户扩展。
    # 只加载声明，不激活——激活发生在每个会话（会话自持 bus/工具表）。
    extensions = ExtensionManager()
    extensions.add("builtin", builtin_extension)
    extensions.add(
        "sandbox", Sandbox().as_extension
    )  # 沙箱：同名覆盖内置工具（须在 builtin 后）
    extensions.add("mcp", mcp.as_extension)  # MCP 远端工具作为扩展声明
    extensions.add("skill", SkillLoader.as_extension)  # 技能清单作为扩展声明
    extensions.add("approval", Approval(conf.tool.approval).as_extension)  # 审批作为扩展
    extensions.discover(
        root=conf.extension.dir,
        enabled=conf.extension.enabled,
        disabled=conf.extension.disabled,
    )
    await extensions.load()
    loaded = [e for e in extensions.extensions if e.error is None]
    logger.info(f"extensions loaded: {len(loaded)}")

    sessions = SessionManager(
        tool_config=conf.tool,
        extensions=loaded,
        provider=provider,
        storage=conf.session.storage,
        path=conf.session.path,
    )
    server = CLIServer(sessions=sessions)
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
