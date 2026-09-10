"""事件总线能力：按事件类型订阅、按实例广播。

Runner 只依赖本面发事件，具体总线实现（infra.EventBus）由装配注入。
订阅用事件类，发射用实例；监听器按注册顺序 await。
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EventBus(Protocol):
    def on(self, event_type: type, handler: Any) -> None: ...

    def off(self, event_type: type, handler: Any) -> None: ...

    async def emit(self, event: Any) -> list[Any]:
        """广播事件实例；返回各监听器结果（供屏障判断）。"""
