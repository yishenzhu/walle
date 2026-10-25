from ...infra import tool_context
from ...spec import Inquiry


async def ask_user(question: str, options: list[str] | None = None):
    """用户指令不明确、需要选择、或需确认重要操作时，向用户提问。
    `options` 提供可选项；用户回答后继续本轮。"""
    ctx = tool_context.get()
    if ctx and ctx.channel:
        return await ctx.channel.call(Inquiry(question=question, options=options))
    return "Error: no channel available for user inquiry"
