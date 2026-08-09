"""通知观察者：消费 notify 流做渲染 / 审计，不参与交互。"""
import logging

from ..schemas import NotificationUnion

logger = logging.getLogger(__name__)


class LogObserver:
    """审计观察者：通知沉淀到结构化日志，供审计 / 回放。"""

    async def __call__(self, n: NotificationUnion) -> None:
        logger.debug("ui_event", extra={"event": n.model_dump(mode="json")})
