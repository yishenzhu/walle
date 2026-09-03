import asyncio
import logging

from .conf import Config
from .infra import setup_logger, setup_telemetry, OpenAIProvider
from .core import (
    Agent,
    EventBus,
    ExtensionRegistry,
    Runner,
    SessionRegistry,
    ToolExecutor,
)
from .channel.cli import CLIChannel
from .tools import Tool, ToolRegistry
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

    tools = await ToolRegistry().initialize(conf)

    # 进程级共享事件总线：Runner 发射生命周期事件，扩展订阅同一实例
    bus = EventBus()
    extensions = ExtensionRegistry(bus=bus, registry=tools)

    # 内置工具作引导扩展先注册（用户扩展后到可同名覆盖）；技能由 Agent 拼提示词、read 加载
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
    await extensions.activate()
    logger.info(f"extensions active: {len(extensions.active)}")

    sessions = SessionRegistry(
        agent_factory=lambda name=None: Agent.load(  # None = default agent
            name, tools=tools.all_tools
        ),
        runner=Runner(executor=ToolExecutor(conf.tool), bus=bus),  # 审批来自 conf.yaml
        storage=conf.session.storage,  # 会话历史跨连接/重启保留
        db_path=conf.session.db_path,
    )
    channel = CLIChannel(registry=sessions)
    await channel.start()

    try:
        # 事件驱动，主协程挂起等待（Ctrl+C 退出）
        await asyncio.Event().wait()
    finally:
        await channel.stop()
        await sessions.close()  # 停机销毁全部会话（关存储）
        await tools.close()  # 关闭进程级资源（MCP 客户端）


if __name__ == "__main__":
    asyncio.run(main())
