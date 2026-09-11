"""事件总线载荷：每个事件即其载荷 dataclass（事件与载荷一体）。

与 `spec/protocol/events.py`（EventBus 能力协议）配对：那里声明"怎么订阅/
广播"，这里定义"广播什么"。

事件 = 一个 dataclass 类。订阅用事件类（bus.on(TurnEndEvent, h)），发射用
实例（bus.emit(TurnEndEvent(...))）。字段即该事件携带的数据，handler 收实例
后以 `.` 访问，有静态类型与补全。

置于 schemas 层（纯数据，无行为）：本模块只引用同层模型，不引用协议
（spec.protocol）与任何实现——协议面依赖 schemas 是单向的，此处反引会成环。
故事件字段只用值类型，不携带 Messages/LLM 等能力对象；需要与会话状态交互的
handler 应从 SessionView / tool_context 取（见 spec.protocol.view）。
"""

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
    """文本输出完成（工具轮不发）。"""

    output: Any
    session_id: str | None


@dataclass
class ToolExecutionStartEvent:
    """工具执行前（preflight 屏障：返回 HookVerdict 可阻止/改写）。"""

    tool_name: str
    arguments: dict[str, Any]
    tool_call_id: str


@dataclass
class ToolExecutionEndEvent:
    """工具执行后（非阻断通知，带结果/错误/耗时）。"""

    tool_name: str
    tool_call_id: str
    result: Any
    error: Any
    elapsed_ms: float | None
