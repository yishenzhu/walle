import asyncio
import logging

from .conf import Config
from .infra import setup_logger, setup_telemetry, OpenAIProvider
from .core import (
    Agent,
    ExtensionRegistry,
    SessionRegistry,
)
from .channel.cli import CLIChannel
from .tools import Tool
from .tools.builtin import ask_user, bash, background, job_result, read

logger = logging.getLogger(__name__)


async def main() -> None:
    """启动 agent 服务端 + CLI 客户端连接通道。

    CLI 客户端需显式启动交互：python -m walle.channel.cli。
    """
    conf = Config.load()
    setup_logger(conf.log)
    setup_telemetry(conf.telemetry)
    OpenAIProvider.load_env()

    # 进程级扩展加载器：内置工具 + .agent/extensions/ 用户扩展。
    # 只加载声明，不激活——激活发生在每个会话（会话自持 bus/工具表）。
    extensions = ExtensionRegistry()
    async def builtin_ext(api) -> None:
        for fn in (bash, ask_user, background, job_result, read):
            api.register_tool(Tool.from_function(fn))

    extensions.add("builtin", builtin_ext)
    extensions.discover(
        root=conf.extension.dir,
        enabled=conf.extension.enabled,
        disabled=conf.extension.disabled,
    )
    await extensions.load()
    loaded = [e for e in extensions.extensions if e.error is None]
    logger.info(f"extensions loaded: {len(loaded)}")

    sessions = SessionRegistry(
        agent_factory=lambda name=None: Agent.load(name),  # 工具源由 Session 绑定会话表
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


if __name__ == "__main__":
    asyncio.run(main())
