"""事件系统：事件基类 + 事件总线（对齐 Cordis 的 EventsService）。"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# 事件监听器：handler(event, value)，value 仅用于 waterfall 传递改写值，其余模式为 None。
Handler = Callable[[Any, Any], Any]


class Event:
    """全部事件的基类：事件实例必须继承本类，才能沿 MRO 命中基类订阅。"""


class Stop:
    """waterfall 主动终断：value 为定格值。"""

    def __init__(self, value: Any = None) -> None:
        self.value = value


class EventBus:
    """事件总线：按事件类注册、按实例分发；host 级共享（根作用域持有）。"""

    def __init__(self) -> None:
        self._listeners: dict[type, list[Handler]] = {}

    def on(self, event: type, handler: Handler) -> Callable[[], None]:
        """订阅事件类（注册序即执行序）；返回退订函数（幂等）。"""
        self._listeners.setdefault(event, []).append(handler)

        def _off() -> None:
            listeners = self._listeners.get(event)
            if listeners and handler in listeners:
                listeners.remove(handler)

        return _off

    def _listeners_for(self, event: Event) -> list[Handler]:
        """命中监听者：按实例类型 MRO 取基类订阅（子类先、基类后）。"""
        out: list[Handler] = []
        for base in inspect.getmro(type(event))[:-1]:
            out.extend(self._listeners.get(base, ()))
        return out

    async def _call(self, handler: Handler, event: Event, value: Any) -> Any:
        """执行单个监听器；异常隔离（记日志，不中断分发链）。"""
        try:
            result = handler(event, value)
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("事件 %r 监听器异常", event)
            return exc

    async def emit(self, event: Event) -> None:
        """广播：顺序调用观察者，异常隔离，不取结果。"""
        for handler in self._listeners_for(event):
            await self._call(handler, event, None)

    async def bail(self, event: Event) -> Any | None:
        """首个表态：返回第一个非 None 结果。"""
        for handler in self._listeners_for(event):
            result = await self._call(handler, event, None)
            if result is not None and not isinstance(result, BaseException):
                return result
        return None

    async def serial(self, event: Event) -> list[Any]:
        """顺序聚合：按序 await 全部，返回结果列表（异常也在内）。"""
        return [await self._call(handler, event, None) for handler in self._listeners_for(event)]

    async def parallel(self, event: Event) -> list[Any]:
        """并发聚合：并行 await 全部，异常隔离（进结果列表）。"""
        handlers = self._listeners_for(event)
        return list(
            await asyncio.gather(
                *(self._call(handler, event, None) for handler in handlers),
                return_exceptions=True,
            )
        )

    async def waterfall(self, event: Event, value: Any = None) -> Any:
        """拦截/改写链：监听器接收 (event, 当前值) 返回新值递进，stop() 终断。"""
        current = value
        for handler in self._listeners_for(event):
            result = await self._call(handler, event, current)
            if isinstance(result, Stop):
                return result.value
            if result is not None and not isinstance(result, BaseException):
                current = result
        return current