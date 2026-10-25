"""事件总线载荷：每个事件即其载荷 dataclass（事件与载荷一体）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .usage import Usage


@dataclass
class SessionStartEvent:
    """会话开始（一次 run 的入口）。"""

    session_id: str | None


@dataclass
class SessionEndEvent:
    """会话结束（一次 run 收尾）。"""

    session_id: str | None


@dataclass
class AgentStartEvent:
    """agent 开始执行（可能随 handoff 多次）。"""

    agent: str
    session_id: str | None


@dataclass
class AgentEndEvent:
    """agent 结束。"""

    agent: str


@dataclass
class TurnStartEvent:
    """一轮模型往返开始。"""

    turn: int
    agent: str


@dataclass
class TurnEndEvent:
    """一轮模型往返结束（消息已落库）。"""

    turn: int
    agent: str
    session_id: str | None
    usage: Usage


@dataclass
class MessageStartEvent:
    """一条用户消息开始处理。"""

    input: str
    session_id: str | None


@dataclass
class MessageDeltaEvent:
    """流式输出增量（delta 文本，逐块发）。"""

    delta: str


@dataclass
class MessageEndEvent:
    """一次 run 的终态事件；工具轮通常 output=None 且 reason=None。"""

    output: Any
    session_id: str | None
    reason: str | None = None


@dataclass
class ToolCallPrepareEvent:
    """模型开始生成工具调用（参数可能尚未完整，工具尚未执行）。"""

    tool_name: str


@dataclass
class ToolExecutionStartEvent:
    """工具执行前（preflight 屏障：返回 HookVerdict 可阻止/改写）。"""

    tool_name: str
    arguments: dict[str, Any]
    tool_call_id: str


@dataclass
class PreExecuteToolEvent:
    """工具执行前拦截（新事件面：监听器返回 dict 或 HookVerdict）。"""

    name: str
    arguments: dict[str, Any]
    tool_call_id: str


@dataclass
class ToolResultEvent:
    """工具执行后（新事件面：结果 / 错误 / 改写后的参数）。"""

    name: str
    arguments: dict[str, Any]
    tool_call_id: str
    result: Any
    error: Any


@dataclass
class ToolExecutionEndEvent:
    """工具执行后（非阻断通知，带结果/错误/耗时）。"""

    tool_name: str
    tool_call_id: str
    result: Any
    error: Any
    elapsed_ms: float | None
