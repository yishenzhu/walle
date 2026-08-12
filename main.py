import argparse
import asyncio
import logging

from .conf import Config
from .infra import setup_logger, setup_telemetry, OpenAIProvider
from .core import Agent, Runner, SessionRouter
from .channel import CLIChannel
from .tools import ToolRegistry

logger = logging.getLogger(__name__)


def _make_runner() -> Runner:
    """构建 Runner（持有默认 provider / executor，供所有会话复用）。"""
    return Runner()


async def main() -> None:
    """启动 agent 服务端 + CLI 客户端连接通道。

    CLI 客户端需显式启动交互：python -m walle.channel.cli。
    """
    conf = Config.load()
    setup_logger(conf.log)
    setup_telemetry(conf.telemetry)
    OpenAIProvider.load_env()

    registry = await ToolRegistry().initialize(conf)

    # 会话路由：按 chat_id 取/建 Session（每会话独立 agent，历史/kernel 隔离）
    cli_ch = CLIChannel()
    router = SessionRouter(
        transport=cli_ch,
        agent_factory=lambda: Agent(
            instruction="You are a helpful assistant.",
            tools=registry.all_tools,   # 工具源：define_tool/add_mcp 实时反映
        ),
        runner=_make_runner(),
    )
    cli_ch.dispatcher = router   # 消息入口绑定（start 前）
    await cli_ch.start()

    try:
        # 事件驱动，主协程挂起等待（Ctrl+C 退出）
        await asyncio.Event().wait()
    finally:
        await router.close()        # 关闭所有会话（kernel + stream task）
        await cli_ch.stop()
        await registry.close()      # 关闭进程级资源（MCP 客户端）


if __name__ == "__main__":
    asyncio.run(main())
