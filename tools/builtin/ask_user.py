from ...infra import tool_context
from ...spec import Inquiry


async def ask_user(question: str, options: list[str] | None = None):
    """Ask the user when instructions are ambiguous or confirmation is needed.
    `options` lists selectable choices; execution resumes after the answer."""
    ctx = tool_context.get()
    if ctx and ctx.channel:
        return await ctx.channel.call(Inquiry(question=question, options=options))
    return "Error: no channel available for user inquiry"
