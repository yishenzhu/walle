"""通知观察者：消费 notify 流做渲染 / 审计，不参与交互。"""
import logging

from ..schemas import NotificationUnion
from .channel import CLIChannel

logger = logging.getLogger(__name__)


class LogObserver:
    """审计观察者：通知沉淀到结构化日志，供审计 / 回放。"""

    async def __call__(self, n: NotificationUnion) -> None:
        logger.debug("ui_event", extra={"event": n.model_dump(mode="json")})


class ConsoleObserver:
    """纯观察者：把 notify 渲染到终端（不参与交互，供无头模式本地调试）。"""

    async def __call__(self, n: NotificationUnion) -> None:
        # 复用 CLI 渲染（icon + 颜色），但仅渲染不交互
        await CLIChannel().notify(n)
