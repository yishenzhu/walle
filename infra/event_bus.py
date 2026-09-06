"""事件总线：按事件类型注册与广播。

事件 = dataclass 类（见 infra.events）。订阅用事件类，发射用实例：

    bus.on(TurnEndEvent, handler)      # handler 收 TurnEndEvent 实例
    await bus.emit(TurnEndEvent(...))  # 按实例类型找订阅者分发

监听器按注册顺序 await，全部 settle 才进入下一步。TOOL_EXECUTION_START
是工具执行前的屏障：任一监听器返回 HookVerdict，上层据此阻止/改写。
单个插件失败仅记录，不毒化核心循环。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

Handler = Callable[[Any], Awaitable[Any]]


class EventBus:
    """进程级事件广播器：按事件类注册，按实例分发。"""

    def __init__(self) -> None:
        self._handlers: dict[type, list[Handler]] = {}

    def on(self, event_type: type, handler: Handler) -> None:
        """订阅一个事件类（注册次序即执行次序）。handler 收该事件实例。"""
        self._handlers.setdefault(event_type, []).append(handler)

    def off(self, event_type: type, handler: Handler) -> None:
        """退订（扩展卸载时精确摘除）。不存在则忽略。"""
        handlers = self._handlers.get(event_type)
        if handlers:
            try:
                handlers.remove(handler)
            except ValueError:
                pass

    async def emit(self, event: Any) -> list[Any]:
        """广播一个事件实例：按实例类型分发给订阅者。

        返回值供上层做屏障判断：ToolExecutionStartEvent 场景下，若任一
        监听器返回 HookVerdict，上层据此阻止工具执行。监听器抛异常时，
        作为结果返回并继续（插件失败不中断）。
        """
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
        """清空全部监听器（停机 / 重载扩展时用）。"""
        self._handlers.clear()

    def handler_count(self) -> dict[type, int]:
        """各事件类监听器数量（调试 / 测试断言用）。"""
        return {t: len(h) for t, h in self._handlers.items()}
