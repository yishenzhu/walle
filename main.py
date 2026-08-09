import asyncio
from .conf import Config
from .infra import setup_logger, setup_telemetry, OpenAIProvider
from .core import Agent, Runner, ToolExecutor, ChannelApprover
from .channel import CLIChannel, FanoutChannel, LogObserver
from .channel.feishu import FeishuObserver
from .schemas import Receive
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

    # 交互消费者 + 主渲染（CLI），附加审计观察者（LogObserver）与飞书推送
    cli = CLIChannel()
    observers: list = [LogObserver()]
    if conf.feishu.webhook:
        observers.append(FeishuObserver(conf.feishu.webhook, conf.feishu.secret))
    channel = FanoutChannel(target=cli, observers=observers)
    runner = Runner(
        channel=channel,
        session=session,
        tool_executor=ToolExecutor(conf.tool, channel=channel, approver=ChannelApprover(channel)),
    )

    # 终止模型：run 期间装 SIGINT handler（Ctrl+C 取消 run）；空闲时默认（Ctrl+C 退出）
    try:
        while True:
            user_input = await channel.call(Receive())
            if not user_input.content:
                break            # 空输入（直接回车）/ EOF / 空闲 Ctrl+C → 退出（统一出口）
            await cli.run_interruptible(runner.run(agent, user_input.content, streamed=True))
    finally:
        await runner.close()        # 关闭会话级资源（kernel + session）
        await registry.close()      # 关闭进程级资源（MCP 客户端）


asyncio.run(main())
