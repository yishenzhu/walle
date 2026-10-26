"""事件系统：事件基类 + 事件总线（对齐 Cordis 的 EventsService）。"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# 事件监听器：接收事件实例，可同步或异步返回任意值。
Handler = Callable[[Any], Any]


class Event:
    """全部事件的基类：事件实例必须继承本类，才能沿 MRO 命中基类订阅。"""


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

    def off(self, event: type, handler: Handler) -> None:
        """退订（兼容旧总线 API）；不存在则忽略。"""
        listeners = self._listeners.get(event)
        if listeners and handler in listeners:
            listeners.remove(handler)

    def has(self, event: type) -> bool:
        """该事件类是否有监听器。"""
        return bool(self._listeners.get(event))

    def clear(self) -> None:
        """清空全部监听器。"""
        self._listeners.clear()

    def _listeners_for(self, event: Any) -> list[Handler]:
        """命中监听者：按实例类型 MRO 取基类订阅（子类先、基类后）。"""
        out: list[Handler] = []
        for base in inspect.getmro(type(event))[:-1]:
            out.extend(self._listeners.get(base, ()))
        return out

    @staticmethod
    async def _call(handler: Handler, event: Any) -> Any:
        """执行单个监听器；异常隔离（记日志并作为结果返回，不中断分发链）。"""
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("事件 %r 监听器异常", event)
            return exc

    async def emit(self, event: Any) -> list[Any]:
        """顺序分发：逐个 await，异常隔离；返回各监听器结果。"""
        return [await self._call(handler, event) for handler in self._listeners_for(event)]

    async def bail(self, event: Any) -> Any | None:
        """首个表态：返回第一个非 None 结果（preflight 屏障）。"""
        for handler in self._listeners_for(event):
            result = await self._call(handler, event)
            if result is not None and not isinstance(result, BaseException):
                return result
        return None

    async def parallel(self, event: Any) -> list[Any]:
        """并发分发：并行 await 全部，异常隔离（进结果列表）。"""
        handlers = self._listeners_for(event)
        return list(
            await asyncio.gather(
                *(self._call(handler, event) for handler in handlers),
                return_exceptions=True,
            )
        )
