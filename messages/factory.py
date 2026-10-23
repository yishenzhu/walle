"""消息存储装配工厂：按配置构造会话历史（投影包装）。

由装配根（SessionManager）调用；Session 只接收构造好的 Messages，
不自行 new 具体存储——换后端 / 测试注入替身都不必改 Session。
"""

from pathlib import Path

from ..spec import Messages
from .file import (
    PROJECTION_FILE,
    FileMessages,
    FileProjectionStore,
    canonical_cwd,
    session_dir_name,
    workspace_dir,
)
from .in_memory import InMemoryMessages
from .projected import ProjectedMessages
from .projection_store import SQLiteProjectionStore
from .sqlite import SQLiteMessages

DEFAULT_DB_PATH = "data/session.db"


def build_history(
    storage: str,
    path: str | Path | None,
    session_id: str,
    cwd: str | Path | None = None,
) -> Messages:
    """构造会话历史：file 集中落盘；sqlite 落数据库；memory 不落盘。

    三种后端都包一层投影视图——模型读到投影，原文在底层全量保留。
    path 按后端解释：file = 会话根（缺省 data/sessions）；sqlite = db 文件
    （缺省 data/session.db）；memory 忽略。file 后端按工作目录分组，但不写进
    用户仓库。
    """
    if storage == "memory":
        return ProjectedMessages(InMemoryMessages())
    if storage == "file":
        target = canonical_cwd(cwd)
        session_dir = workspace_dir(target, path) / session_dir_name(session_id)
        return ProjectedMessages(
            FileMessages(session_dir, session_id=session_id, cwd=target),
            projection_store=FileProjectionStore(session_dir / PROJECTION_FILE),
        )
    db_path = str(path) if path else DEFAULT_DB_PATH
    return ProjectedMessages(
        SQLiteMessages(db_path=db_path, session_id=session_id),
        projection_store=SQLiteProjectionStore(db_path=db_path, session_id=session_id),
    )


def build_ephemeral_history() -> Messages:
    """一次性会话历史（子 agent 隔离用）：纯内存，不落盘、不跨实例。"""
    return ProjectedMessages(InMemoryMessages())
