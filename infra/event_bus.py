"""事件总线：agent 生命周期与工具钩子的有序屏障。

监听器按注册顺序 await，全部 settle 才进入下一步。before_tool_call /
after_tool_call 是工具执行前后的屏障，任一 before hook 返回 False 则阻止
该工具执行。单个插件失败仅记录，不毒化核心循环。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

Handler = Callable[..., Awaitable[Any]]


class Event(StrEnum):
    """agent 生命周期事件名（session/agent/turn/message/tool）。"""

    SESSION_START = "session_start"
    SESSION_END = "session_end"
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    MESSAGE_START = "message_start"
    MESSAGE_END = "message_end"
    TOOL_EXECUTION_START = "tool_execution_start"
    TOOL_EXECUTION_END = "tool_execution_end"


class EventBus:
    """进程级事件广播器：按注册顺序 await 监听器，失败收集不中断。"""

    def __init__(self) -> None:
        self._handlers: dict[Event, list[Handler]] = {e: [] for e in Event}

    def on(self, event: Event, handler: Handler) -> None:
        """订阅一个事件（注册次序即执行次序）。未知事件名报错。"""
        if event not in self._handlers:
            raise ValueError(f"unknown event: {event}")
        self._handlers[event].append(handler)

    async def emit(self, event: Event, **ctx: Any) -> list[Any]:
        """按注册顺序 await 全部监听器。

        返回值供上层做屏障判断：before_tool_call 场景下，若任一监听器
        返回 False，上层据此阻止工具执行。监听器抛异常时，作为结果返回
        并继续（插件失败不中断）。
        """
        results: list[Any] = []
        for handler in self._handlers[event]:
            try:
                results.append(await handler(**ctx))
            except Exception as exc:  # noqa: BLE001 - 插件失败隔离
                results.append(exc)
        return results

    def has(self, event: Event) -> bool:
        """该事件是否有监听器（避免空转 emit）。"""
        return bool(self._handlers[event])

    def clear(self) -> None:
        """清空全部监听器（停机 / 重载扩展时用）。"""
        for handlers in self._handlers.values():
            handlers.clear()

    def handler_count(self) -> dict[Event, int]:
        """各事件监听器数量（调试 / 测试断言用）。"""
        return {e: len(h) for e, h in self._handlers.items()}
