import asyncio
import logging

from .conf import Config
from .infra import setup_logger, setup_telemetry, OpenAIProvider
from .core import (
    ExtensionRegistry,
    SessionRegistry,
)
from .channel.cli import CLIChannel
from .tools import MCPRegistry, Approval
from .tools.builtin.extension import builtin_ext
from .tools.skill import Skill

logger = logging.getLogger(__name__)


async def main() -> None:
    """启动 agent 服务端 + CLI 客户端连接通道。

    CLI 客户端需显式启动交互：python -m walle.channel.cli。
    """
    conf = Config.load()
    setup_logger(conf.log)
    setup_telemetry(conf.telemetry)
    OpenAIProvider.load_env()

    # 进程级共享 MCP 客户端：连接一次，组装成"mcp"扩展进扩展池
    mcp = MCPRegistry()
    await mcp.connect()

    # 进程级扩展加载器：内置工具扩展 + MCP 扩展 + 技能 + 审批 + .agent/extensions/ 用户扩展。
    # 只加载声明，不激活——激活发生在每个会话（会话自持 bus/工具表）。
    extensions = ExtensionRegistry()
    extensions.add("builtin", builtin_ext)
    extensions.add("mcp", mcp.as_ext)  # MCP 远端工具作为扩展声明
    extensions.add("skill", Skill.as_ext)  # 技能清单作为扩展声明
    extensions.add("approval", Approval(conf.tool.approval).as_ext)  # 审批作为扩展
    extensions.discover(
        root=conf.extension.dir,
        enabled=conf.extension.enabled,
        disabled=conf.extension.disabled,
    )
    await extensions.load()
    loaded = [e for e in extensions.extensions if e.error is None]
    logger.info(f"extensions loaded: {len(loaded)}")

    sessions = SessionRegistry(
        tool_config=conf.tool,
        extensions=loaded,
        storage=conf.session.storage,
        db_path=conf.session.db_path,
    )
    channel = CLIChannel(registry=sessions)
    await channel.start()

    try:
        # 事件驱动，主协程挂起等待（Ctrl+C 退出）
        await asyncio.Event().wait()
    finally:
        await channel.stop()
        await sessions.close()  # 停机销毁全部会话（关存储/作业）
        await mcp.close()  # 关闭进程级 MCP 客户端


if __name__ == "__main__":
    asyncio.run(main())
