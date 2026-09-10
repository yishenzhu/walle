"""消息存储装配工厂：按配置构造会话历史（投影包装）。

由装配根（SessionRegistry）调用；Session 只接收构造好的 Messages，
不自行 new 具体存储——换后端 / 测试注入替身都不必改 Session。
"""

from ..spec import Messages
from .in_memory import InMemoryMessages
from .meta import SQLiteProjectionStore
from .projected import ProjectedMessages
from .sqlite import SQLiteMessages


def build_history(storage: str, db_path: str, session_id: str) -> Messages:
    """构造会话历史：memory 用内存；sqlite 持久化原文 + 切点落盘。

    两种后端都包一层投影视图——模型读到投影，原文在底层全量保留。
    """
    if storage == "memory":
        return ProjectedMessages(InMemoryMessages())
    return ProjectedMessages(
        SQLiteMessages(db_path=db_path, session_id=session_id),
        projection_store=SQLiteProjectionStore(db_path=db_path, session_id=session_id),
    )


def build_ephemeral_history() -> Messages:
    """一次性会话历史（子 agent 隔离用）：纯内存，不落盘、不跨实例。"""
    return ProjectedMessages(InMemoryMessages())
