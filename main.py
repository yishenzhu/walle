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

    registry = await ToolRegistry().load_mcp(conf.mcp)

    all_tools = registry.builtin_tools()
    all_tools.extend(registry.mcp_tools())

    agent = Agent(
        instruction="You are a helpful assistant.",
        tools=all_tools,
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
        tool_executor=ToolExecutor(conf.approval),
    )

    try:
        while True:
            user_input = await channel.receive()
            if user_input.content.strip() == ":q":
                break
            await runner.run(agent, user_input.content, streamed=True)
    finally:
        await session.close()
        await registry.close()


asyncio.run(main())
