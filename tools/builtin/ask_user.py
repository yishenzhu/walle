from .. import tool_context


async def ask_user(question: str, options: list[str] | None = None):
    """用户指令不明确、需要选择、或需确认重要操作时，优先使用此工具向用户提问。"""
    ctx = tool_context.get()
    if ctx and ctx.interact:
        return await ctx.interact.ask(question=question, options=options)
    return "Error: no channel available for user inquiry"
