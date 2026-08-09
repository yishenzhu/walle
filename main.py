import argparse
import asyncio
from .conf import Config
from .infra import setup_logger, setup_telemetry, OpenAIProvider
from .core import Agent, Runner, ToolExecutor, ChannelApprover
from .channel import CLIChannel, FanoutChannel, LogObserver, FeishuChannel
from .schemas import Receive
from .tools import ToolRegistry
from .session import (
    InMemorySession,
    CompressibleSession,
    SummaryCompressor,
    PromptLimitPolicy,
)


async def main(channel: str = "cli"):

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

    # 交互主通道：--channel feishu 用飞书（长连接收发），否则 CLI
    cli = CLIChannel()
    if channel == "feishu":
        if not conf.feishu.app_id:
            raise ValueError("feishu 模式需要 conf.yaml 配置 feishu.app_id / app_secret")
        target = FeishuChannel(conf.feishu.app_id, conf.feishu.app_secret)
        await target.start()
        observers: list = [LogObserver()]  # 只保留日志，本地 CLI 不渲染
    else:
        target = cli
        observers = [LogObserver()]
    fanout = FanoutChannel(target=target, observers=observers)
    runner = Runner(
        channel=fanout,
        session=session,
        tool_executor=ToolExecutor(conf.tool, channel=fanout, approver=ChannelApprover(fanout)),
    )

    # 终止模型：run 期间装 SIGINT handler（Ctrl+C 取消 run）；空闲时默认（Ctrl+C 退出）
    try:
        while True:
            user_input = await fanout.call(Receive())
            if not user_input.content:
                break            # 空输入（直接回车）/ EOF / 空闲 Ctrl+C → 退出（统一出口）
            await cli.run_interruptible(runner.run(agent, user_input.content, streamed=True))
    finally:
        await runner.close()        # 关闭会话级资源（kernel + session）
        await registry.close()      # 关闭进程级资源（MCP 客户端）


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="walle agent")
    parser.add_argument(
        "--channel",
        choices=["cli", "feishu"],
        default="cli",
        help="交互通道：cli（默认）或 feishu",
    )
    args = parser.parse_args()
    asyncio.run(main(args.channel))
