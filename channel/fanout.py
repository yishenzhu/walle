"""FanoutChannel：通知侧 fan-out 到 N 个观察者，交互侧仍 1:1 直连。"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ..schemas import NotificationUnion, ServiceUnion
from .channel import Channel

logger = logging.getLogger(__name__)

Observer = Callable[[NotificationUnion], Awaitable[None]]


class FanoutChannel:
    """通知侧 fan-out，交互侧直连主消费者。

    主消费者（target）同时承担交互（call）与主渲染（notify）；
    observers 是附加的只读观察者（审计日志 / Web 面板 / 指标）。
    """

    def __init__(
        self,
        target: Channel,
        observers: list[Observer] | None = None,
    ):
        self._target = target
        self._observers: list[Observer] = observers or []

    async def notify(self, n: NotificationUnion) -> None:
        # 主消费者 + N 个观察者，失败不拖垮主链路
        results = await asyncio.gather(
            self._target.notify(n),
            *(o(n) for o in self._observers),
            return_exceptions=True,
        )
        for sink, res in zip([self._target, *self._observers], results):
            if isinstance(res, Exception):
                logger.warning(f"observer {sink!r} failed: {res}")

    async def call(self, s: ServiceUnion) -> Any:
        # 交互永远 1:1，直连主消费者
        return await self._target.call(s)
