"""Channel 协议：服务端与传输层的唯一稳定契约。

Runner / Executor / 工具只认识 notify（广播）与 call（点对点）两个原语，
实现可替换（CLI / 飞书 / 测试）。
"""
from typing import Any, Protocol, runtime_checkable

from ..schemas import NotificationUnion, ServiceUnion


@runtime_checkable
class Channel(Protocol):
    async def notify(self, notification: NotificationUnion) -> None: ...  # 广播，无返回
    async def call(self, service: ServiceUnion) -> Any: ...               # 点对点，有返回


@runtime_checkable
class SessionRegistry(Protocol):
    """会话注册表协议：CLIChannel 对会话管理的唯一依赖（duck typing）。

    实现（core.SessionRegistry）由上层注入，channel 层不依赖 core 实现类，
    避免低层依赖高层。CLIChannel 只用 get/register/create/list；
    remove/close 供上层停机清理使用。
    """

    def get(self, session_id: str) -> Any: ...
    def create(self, conn: Any) -> Any: ...   # 新建会话（经注入 factory）并注册
    def register(self, session: Any) -> None: ...
    def list(self) -> list[dict]: ...
    def remove(self, session_id: str) -> Any: ...
    async def close(self) -> None: ...


def truncate(value: Any, limit: int) -> str:
    """展示截断：超限加省略号（工具参数 / 结果渲染用）。"""
    s = str(value)
    return s if len(s) <= limit else s[:limit] + "..."
