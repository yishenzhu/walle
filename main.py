import asyncio
import logging

from .conf import Config
from .infra import setup_logger, setup_telemetry, OpenAIProvider
from .core import Agent, Runner, SessionRegistry, ToolExecutor
from .channel.cli import CLIChannel
from .tools import ToolRegistry

logger = logging.getLogger(__name__)


async def main() -> None:
    """启动 agent 服务端 + CLI 客户端连接通道。

    CLI 客户端需显式启动交互：python -m walle.channel.cli。
    """
    conf = Config.load()
    setup_logger(conf.log)
    setup_telemetry(conf.telemetry)
    OpenAIProvider.load_env()

    tools = await ToolRegistry().initialize(conf)

    sessions = SessionRegistry(
        # 闭包：只接受 agent 名（None = default），路径拼接/校验由 Agent.load 负责
        agent_factory=lambda name=None: Agent.load(
            name, tools=tools.all_tools,   # 工具源：define_tool/add_mcp 实时反映
        ),
        # 审批规则来自 conf.yaml：runner 默认 ToolExecutor() 无配置，
        # 会退化为全量 ASK（allow 规则失效），必须显式传入。
        runner=Runner(executor=ToolExecutor(conf.tool)),
        # 会话持久化：历史跨连接/重启保留（attach/resume 的基础）
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
        await sessions.close()          # 停机销毁全部会话（关 kernel/存储）
        await tools.close()             # 关闭进程级资源（MCP 客户端）


if __name__ == "__main__":
    asyncio.run(main())
