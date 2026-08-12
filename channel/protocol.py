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


def truncate(value: Any, limit: int) -> str:
    """展示截断：超限加省略号（工具参数 / 结果渲染用）。"""
    s = str(value)
    return s if len(s) <= limit else s[:limit] + "..."
