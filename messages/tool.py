"""消息层工具：history（只读回源）+ new_window（模型主动硬切窗口）。"""

from ..infra import tool_context
from ..schemas import Projection, ToolMessage, UserMessage

MAX_LIMIT = 50
DEFAULT_LIMIT = 20


async def history(query: str = "", limit: int = DEFAULT_LIMIT) -> str:
    """Search this session's raw conversation history (read-only, newest first).

    Use to recall messages that were cut out of the model window verbatim.
    Args:
        query: Space-separated words, all must appear (case-insensitive); empty = recent.
        limit: Max messages (default 20, max 50).
    """
    ctx = tool_context.get()
    if ctx is None or ctx.history is None:
        return "Error: no session history available"
    limit = max(1, min(int(limit), MAX_LIMIT))

    matches = await ctx.history.search(query=query, limit=limit)
    total = await ctx.history.count()
    if not matches:
        label = f" for '{query}'" if query else ""
        return f"(history: 0 matched{label}, total {total} messages)"

    def line(m):
        return (
            f"[tool:{m.tool_call_id}] {m.content}"
            if isinstance(m, ToolMessage)
            else f"[{m.role}] {m.content or ''}"
        )

    head = f"(history: {len(matches)} matched of {total} messages, newest first)"
    return "\n".join([head, *[line(m) for m in matches]])


async def new_window(reason: str = "") -> str:
    """Hard-cut to a fresh window (older turns leave the model view).

    Call after writing anything still needed into .agent/note.md via `edit`.
    Args:
        reason: Why resetting (e.g. 'segment done, continuing next phase').
    """
    ctx = tool_context.get()
    if ctx is None or ctx.history is None:
        return "Error: no session history available"
    history = ctx.history
    if not isinstance(history, Projection):
        return "Error: this session history does not support window reset"
    raw = await history.underlying.get()
    if not raw:
        return "Error: empty history"

    # 新窗口起点 = 最后一个 UserMessage（当前轮）；切点只前进
    last_user = max(
        (i for i, m in enumerate(raw) if isinstance(m, UserMessage)), default=None
    )
    if last_user is None:
        return "Error: no user turn boundary found"
    old = await history.projection()
    old_cut = old if old is not None else 0
    new_cut = max(last_user, old_cut)
    if new_cut <= old_cut:
        return "(window already at a fresh boundary; nothing to reset)"

    await history.set_projection(new_cut)  # 纯截断：旧轮移出模型视野
    kept = len(raw) - new_cut
    note = f" (reason: {reason})" if reason else ""
    return (
        f"(window reset: fresh window{note}; {kept} messages remain visible — "
        f"the current turn. Recall anything older via `history`.)"
    )
