"""内置插件：channel（事件 → 客户端通知）与 executor（工具执行）。"""

from __future__ import annotations

from ..conf import TimeoutConfig
from ..plugin import plugin
from ..spec import (
    Channel,
    Delta,
    DeltaEnd,
    ExecutorService,
    MessageDeltaEvent,
    MessageEndEvent,
    ToolCallPrepareEvent,
    ToolPrepare,
)
from .executor import ToolExecutor


@plugin(name="channel", inject=(Channel,))
async def channel_plugin(ctx, config) -> None:
    """订阅 Runner 事件，转成当前连接的客户端通知。"""
    channel = ctx.get(Channel)
    ctx.on(MessageDeltaEvent, lambda evt: channel.notify(Delta(delta=evt.delta)))
    ctx.on(MessageEndEvent, lambda evt: channel.notify(DeltaEnd(reason=evt.reason)))
    ctx.on(
        ToolCallPrepareEvent,
        lambda evt: channel.notify(ToolPrepare(tool_name=evt.tool_name)),
    )


@plugin(name="executor")
async def executor_plugin(ctx, config) -> None:
    """工具执行器插件：preflight / 超时 / 通知 / 后台作业。"""
    # 插件配置形如 {"timeout": {...}}，直接校验整个 config 会把 extra 丢掉、退回默认值
    timeout_config = config.get("timeout") if isinstance(config, dict) else None
    await ctx.register(
        ExecutorService, ToolExecutor(TimeoutConfig.model_validate(timeout_config or {}))
    )
