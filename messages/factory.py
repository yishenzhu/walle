"""消息存储装配工厂：按配置构造会话历史（投影包装）。"""

from pathlib import Path

from ..spec import Messages
from .file import (
    FileMessages,
    canonical_cwd,
    session_dir_name,
    workspace_dir,
)
from .in_memory import InMemoryMessages
from .projected import ProjectedMessages
from .projection_store import PROJECTION_FILE, FileProjectionStore, SQLiteProjectionStore
from .sqlite import DEFAULT_DB_PATH, SQLiteMessages


def build_history(
    storage: str,
    path: str | Path | None,
    session_id: str,
    cwd: str | Path | None = None,
) -> Messages:
    """构造会话历史：file 集中落盘；sqlite 落数据库；memory 不落盘。"""
    if storage == "memory":
        return _memory_history()
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


def _memory_history() -> Messages:
    """纯内存历史：不落盘、不跨实例。"""
    return ProjectedMessages(InMemoryMessages())


def build_ephemeral_history() -> Messages:
    """一次性会话历史（子 agent 隔离用）。"""
    return _memory_history()
