"""消息层工具：history（只读回源）+ new_window（模型主动硬切窗口）。"""

from ..infra import tool_context
from ..schemas import Projection, ToolMessage, UserMessage

MAX_LIMIT = 50
DEFAULT_LIMIT = 20


async def history(
    query: str = "",
    offset: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> str:
    """Read this session's raw conversation history (read-only).

    Keyword search (newest first) or, when `offset` is set, a message-index
    window from the oldest message upward.
    Args:
        query: Space-separated words, all must appear (case-insensitive); empty = recent.
        offset: Start at this message index (0 = oldest); ignores query, ascending.
        limit: Max messages (default 20, max 50).
    """
    ctx = tool_context.get()
    if ctx is None or ctx.history is None:
        return "Error: no session history available"
    limit = max(1, min(int(limit), MAX_LIMIT))
    total = await ctx.history.count()

    def line(m, index=None):
        prefix = f"[{index}] " if index is not None else ""
        return (
            f"{prefix}[tool:{m.tool_call_id}] {m.content}"
            if isinstance(m, ToolMessage)
            else f"{prefix}[{m.role}] {m.content or ''}"
        )

    if offset is not None:
        # 序号窗口：正序取 [offset, offset+limit)，每条带绝对序号
        offset = max(0, int(offset))
        matches = await ctx.history.query(offset, limit)
        if not matches:
            return f"(history: no messages at index {offset} of {total})"
        head = f"(history: messages {offset}..{offset + len(matches) - 1} of {total}, ascending)"
        return "\n".join(
            [head, *[line(m, offset + i) for i, m in enumerate(matches)]]
        )

    matches = await ctx.history.search(query=query, limit=limit)
    if not matches:
        label = f" for '{query}'" if query else ""
        return f"(history: 0 matched{label}, total {total} messages)"
    head = f"(history: {len(matches)} matched of {total} messages, newest first)"
    return "\n".join([head, *[line(m, i) for i, m in matches]])


async def new_window() -> str:
    """Hard-cut to a fresh window (older turns leave the model view).

    Call after writing anything still needed into .agent/note.md via `edit`.
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
    return (
        f"(window reset: fresh window; {kept} messages remain visible — "
        f"the current turn. Recall anything older via `history`.)"
    )
