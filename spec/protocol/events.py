"""事件总线能力：按事件类型订阅、按实例广播。"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EventBus(Protocol):
    def on(self, event_type: type, handler: Any) -> Any: ...

    def off(self, event_type: type, handler: Any) -> None: ...

    async def emit(self, event: Any) -> list[Any]:
        """顺序分发；返回各监听器结果（供观测/兼容使用）。"""

    async def bail(self, event: Any) -> Any | None:
        """首个非 None 表态（preflight 屏障）。"""

    async def parallel(self, event: Any) -> list[Any]:
        """并发分发；返回各监听器结果。"""
