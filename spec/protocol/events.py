"""事件总线能力：按事件类型订阅、按实例广播。"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EventBus(Protocol):
    def on(self, event_type: type, handler: Any) -> None: ...

    def off(self, event_type: type, handler: Any) -> None: ...

    async def emit(self, event: Any) -> list[Any]:
        """广播事件实例；返回各监听器结果（供屏障判断）。"""
