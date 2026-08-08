import asyncio
import readline
from .conf import Config
from .infra import setup_logger, setup_telemetry, OpenAIProvider
from .core import Agent, Runner, ToolExecutor
from .channel import CLIChannel
from .tools import ToolRegistry
from .session import (
    InMemorySession,
    CompressibleSession,
    SummaryCompressor,
    PromptLimitPolicy,
)


async def main():

    conf = Config.load()
    setup_logger(conf.log)
    setup_telemetry(conf.telemetry)
    OpenAIProvider.load_env()

    registry = await ToolRegistry().initialize(conf)

    agent = Agent(
        instruction="You are a helpful assistant.",
        tools=registry.all_tools,   # 工具源：define_tool/add_mcp 实时反映
    )

    session = CompressibleSession(
        session=InMemorySession(),
        policy=PromptLimitPolicy(),
        compressor=SummaryCompressor(),
    )

    channel = CLIChannel()
    runner = Runner(
        channel=channel,
        session=session,
        tool_executor=ToolExecutor(conf.tool),
    )

    try:
        while True:
            user_input = await channel.receive()
            if user_input.content.strip() == ":q":
                break
            await runner.run(agent, user_input.content, streamed=True)
    finally:
        await runner.close()        # 关闭会话级资源（kernel + session）
        await registry.close()      # 关闭进程级资源（MCP 客户端）


asyncio.run(main())
