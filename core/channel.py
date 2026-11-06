"""channel 插件：把 Runner 事件映射为客户端通知（可替换的展示策略）。"""

from __future__ import annotations

from ..plugin import plugin
from ..spec import (
    Channel,
    Delta,
    DeltaEnd,
    MessageDeltaEvent,
    MessageEndEvent,
    ToolCallPrepareEvent,
    ToolPrepare,
)


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
