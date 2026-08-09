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


def render_notification(n: NotificationUnion) -> str:
    """渲染单条通知为纯文本，跨 UI 复用（CLI / 飞书）。

    - Delta：增量文本（调用方决定是否换行）
    - DeltaEnd：回合边界，返回空串（调用方自行处理换行）
    - 其余：整条消息文本
    """
    match n:
        case Delta(delta=delta):
            return delta
        case DeltaEnd():
            return ""
        case ToolStart(tool_name=name, arguments=args):
            return f"[调用工具 {name}({args})]"
        case ToolResult(tool_call_id=tc_id, result=result, error=None):
            return f"[工具结果 {tc_id}] {truncate(result, 512)}"
        case ToolResult(tool_call_id=tc_id, error=err):
            return f"[工具错误 {tc_id}] {err}"
        case Error(message=msg):
            return f"[错误] {msg}"
    return ""


def truncate(value: Any, limit: int) -> str:
    s = str(value)
    return s if len(s) <= limit else s[:limit] + "..."
