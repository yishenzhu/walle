"""通知渲染：把 NotificationUnion 渲染为可展示文本，CLI / 飞书等 UI 复用。"""
from typing import Any

from ..schemas import (
    Delta,
    DeltaEnd,
    Error,
    NotificationUnion,
    ToolResult,
    ToolStart,
)


def render_notification(n: NotificationUnion) -> tuple[str, str]:
    """渲染单条通知为 (文本, end)，end 可直接作为 print 的 end 参数。

    - Delta：增量，不换行（end=""）
    - DeltaEnd：回合结束，仅换行（text=""）
    - 其余：整条消息，换行
    """
    match n:
        case Delta(delta=delta):
            return delta, ""
        case DeltaEnd():
            return "", "\n"
        case ToolStart(tool_name=name, arguments=args):
            return f"[调用工具 {name}({args})]", "\n"
        case ToolResult(tool_call_id=tc_id, result=result, error=None):
            return f"[工具结果 {tc_id}] {truncate(result, 512)}", "\n"
        case ToolResult(tool_call_id=tc_id, error=err):
            return f"[工具错误 {tc_id}] {err}", "\n"
        case Error(message=msg):
            return f"[错误] {msg}", "\n"
    return "", "\n"


def truncate(value: Any, limit: int) -> str:
    s = str(value)
    return s if len(s) <= limit else s[:limit] + "..."
