"""课程续跑：启动时把未完成的课程接回无连接会话。"""

from __future__ import annotations

import logging
from typing import Any

from ..spec import ModelConfig
from .store import list_courses, load_course, mark_course

logger = logging.getLogger(__name__)


async def resume_pending_courses(manager: Any, cfg: ModelConfig) -> int:
    """为未完成课程重建会话：浏览器没开也能继续生成。

    `manager` 只要求提供 get / create_headless / provider_for 三个方法，
    避免 learning 层反向依赖 core 的具体实现。
    """
    resumed = 0
    for item in list_courses():
        course = load_course(item["id"]) or {}
        status = course.get("status")
        info = course.get("driver") or {}
        restarted = status == "paused" and info.get("last_error") == "agent restarted"
        if status != "generating" and not restarted:
            continue

        session_id = course.get("session_id")
        cwd = course.get("workspace")
        if not session_id or not cwd:
            mark_course(
                item["id"], "paused", last_error="缺少会话信息，无法自动续跑"
            )
            continue
        if manager.get(session_id) is not None:
            continue

        session = manager.create_headless(session_id, cwd, agent="learning")
        session.set_provider(manager.provider_for(cfg, session_id))
        await session.start()
        resumed += 1
        logger.info(f"course resumed headless: {item['id']} session={session_id}")
    return resumed
