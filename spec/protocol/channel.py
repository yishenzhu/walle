"""通道与交互协议：服务端/传输层与上层会话的唯一稳定契约。

从 notify（广播）/ call（点对点）两个原语，到会话身份与管理能力，
本模块只声明能力面，不涉及任何具体实现。
"""

from typing import Any, Protocol, runtime_checkable

from ..schemas import NotificationUnion, ServiceUnion, SessionConn


@runtime_checkable
class Channel(Protocol):
    async def notify(self, notification: NotificationUnion) -> None: ...  # 广播，无返回
    async def call(self, service: ServiceUnion) -> Any: ...               # 点对点，有返回


@runtime_checkable
class Sessions(Protocol):
    """会话管理能力：get / create / register / list / remove / close。

    使用方只依赖本能力面，不 import 具体实现；remove/close 供停机清理。
    """

    def get(self, session_id: str) -> Any: ...
    def create(self, conn: SessionConn, ext_names: list[str] | None = None) -> Any: ...
    def register(self, session: Any) -> None: ...
    def list(self) -> list[dict]: ...
    def remove(self, session_id: str) -> Any: ...
    async def close(self) -> None: ...