"""事件总线：按事件类型注册与广播。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

Handler = Callable[[Any], Awaitable[Any]]


class EventBus:
    """会话级事件广播器：按事件类注册，按实例分发。"""

    def __init__(self) -> None:
        self._handlers: dict[type, list[Handler]] = {}

    def on(self, event_type: type, handler: Handler) -> None:
        """订阅一个事件类（注册次序即执行次序）。handler 收该事件实例。"""
        self._handlers.setdefault(event_type, []).append(handler)

    def off(self, event_type: type, handler: Handler) -> None:
        """退订（插件卸载时精确摘除）。不存在则忽略。"""
        handlers = self._handlers.get(event_type)
        if handlers and handler in handlers:
            handlers.remove(handler)

    async def emit(self, event: Any) -> list[Any]:
        """广播一个事件实例：按实例类型分发给订阅者。"""
        handlers = self._handlers.get(type(event), [])
        results: list[Any] = []
        for handler in handlers:
            try:
                results.append(await handler(event))
            except Exception as exc:  # noqa: BLE001 - 插件失败隔离
                results.append(exc)
        return results

    def has(self, event_type: type) -> bool:
        """该事件类是否有监听器（避免空转 emit）。"""
        return bool(self._handlers.get(event_type))

    def clear(self) -> None:
        """清空全部监听器（停机 / 重载插件时用）。"""
        self._handlers.clear()
