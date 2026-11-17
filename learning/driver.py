"""课程生成驱动器：一次只推进一课，失败退避，超限暂停。"""

from __future__ import annotations

import asyncio
import logging
import os
import re

from ..spec import Scheduler, SessionView
from .store import (
    lesson_file,
    list_courses,
    load_course,
    mark_course,
    next_pending_lesson,
    written_progress,
)

logger = logging.getLogger(__name__)

BACKOFF = (5.0, 15.0, 45.0)

# 这些失败重试没有意义：密钥无效、无权限、模型不存在、上下文超限、配额耗尽
FATAL = re.compile(
    r"\b(400|401|403|404)\b"
    r"|invalid.?_?api.?_?key|unauthoriz|authenticat|permission"
    r"|insufficient.?_?quota|context.{0,12}(length|window)|model_not_found",
    re.IGNORECASE,
)


def is_retryable(error: str | None) -> bool:
    """上游失败是否值得退避重试；未知错误按可重试处理。"""
    if not error:
        return True
    return FATAL.search(error) is None


def course_tick() -> float:
    return float(os.getenv("WALLE_COURSE_TICK", "60"))


def course_stall() -> float:
    return float(os.getenv("WALLE_COURSE_STALL", "900"))


class CourseDriver:
    """按课程状态推进生成：空闲时提交「只写一课」的 run，写完再推进下一课。"""

    def __init__(
        self,
        scheduler: Scheduler,
        session_id: str,
        view: SessionView | None = None,
        root=None,
        tick: float | None = None,
        stall: float | None = None,
        max_attempts: int = 3,
    ):
        self._scheduler = scheduler
        self._session_id = session_id
        self._view = view
        self._root = root
        self._tick = course_tick() if tick is None else tick
        self._stall = course_stall() if stall is None else stall
        self._max_attempts = max_attempts
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.scan()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 驱动不能因单课失败退出
                logger.warning(f"course driver scan failed: {exc!r}")
            await asyncio.sleep(self._tick)

    async def scan(self) -> None:
        """扫描本会话的课程：续跑暂停的、推进生成中的。"""
        for item in list_courses(self._root):
            if item.get("session_id") not in (None, self._session_id):
                continue
            course = load_course(item["id"], self._root)
            if course is None:
                continue
            status = course.get("status")
            if status == "paused":
                if not self._resumable(course):
                    continue
                course = mark_course(
                    item["id"], "generating", root=self._root, last_error=None
                )
                if course is None:
                    continue
            elif status != "generating":
                continue
            await self.drive(item["id"])

    def _resumable(self, course: dict) -> bool:
        """只有「进程重启」造成的暂停可以自动续跑；失败超限的暂停不碰。"""
        info = course.get("driver") or {}
        return info.get("last_error") == "agent restarted" and self._scheduler.has_model

    async def drive(self, course_id: str) -> None:
        while True:
            course = load_course(course_id, self._root)
            if course is None or course.get("status") != "generating":
                return
            pending = next_pending_lesson(course, self._root)
            if pending is None:
                mark_course(course_id, "done", root=self._root)
                return

            lesson_id = pending["id"]
            info = course.get("driver") or {}
            attempts = dict(info.get("attempts") or {})
            count = int(attempts.get(lesson_id, 0))
            if count >= self._max_attempts:
                mark_course(
                    course_id,
                    "paused",
                    root=self._root,
                    current=lesson_id,
                    last_error=f"同一课连续失败 {self._max_attempts} 次",
                )
                logger.warning(f"course paused after {count} attempts: {course_id}")
                return

            if not self._scheduler.has_model:
                return

            if count:
                await asyncio.sleep(BACKOFF[min(count - 1, len(BACKOFF) - 1)])

            # 每课一个干净窗口：模型只看到课程计划 + 本课要求，不背前几课全文
            await self._reset_window()

            task = self._scheduler.submit_background(self._prompt(course, pending))
            if task is None:
                return  # 会话忙或已关闭，下一轮再试

            attempts[lesson_id] = count + 1
            mark_course(
                course_id,
                "generating",
                root=self._root,
                current=lesson_id,
                attempts=attempts,
                last_error=None,
            )
            try:
                await asyncio.wait_for(task, self._stall)
            except TimeoutError:
                logger.warning(f"course lesson stalled: {course_id}/{lesson_id}")
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

            if lesson_file(course_id, lesson_id, self._root).exists():
                continue

            error = self._scheduler.last_error
            if not is_retryable(error):
                mark_course(
                    course_id,
                    "paused",
                    root=self._root,
                    current=lesson_id,
                    last_error=f"模型不可用，已停止：{(error or '')[:120]}",
                )
                logger.warning(f"course paused on fatal error: {course_id}: {error!r}")
                return
            logger.info(f"lesson retry {count + 1}/{self._max_attempts}: {lesson_id}")

    async def _reset_window(self) -> None:
        """把历史切到当前点：旧轮移出模型视野，原文仍在磁盘。"""
        history = getattr(self._view, "history", None)
        setter = getattr(history, "set_projection", None)
        if setter is None:
            return
        try:
            await setter(await history.count())
        except Exception as exc:  # noqa: BLE001 - 切窗口失败不该中断生成
            logger.warning(f"course window reset failed: {exc!r}")

    def _prompt(self, course: dict, lesson: dict) -> str:
        written, total = written_progress(course, self._root)
        lines = [
            f"课程《{course.get('title')}》（course_id={course['id']}），"
            f"共 {total} 课，已写 {written} 课。"
        ]
        if course.get("goal"):
            lines.append(f"目标：{course['goal']}")
        outcomes = (course.get("outcome") or {}).get("can_do") or []
        if outcomes:
            lines.append("学完能做到：" + "；".join(outcomes))
        lines.append("大纲：")
        lessons = sorted(
            course.get("lessons") or [], key=lambda item: item.get("order") or 0
        )
        for item in lessons:
            done = lesson_file(course["id"], item.get("id"), self._root).exists()
            mark = "已完成" if done else ("本轮" if item["id"] == lesson["id"] else "待写")
            lines.append(f"- [{mark}] {item.get('order')}. {item.get('title')}")
        lines.extend(
            [
                "",
                f"本轮只写第 {lesson.get('order')} 课《{lesson.get('title')}》"
                f"（lesson_id={lesson['id']}）。",
                "要求：正文（场景 / 可运行示例 / 1-3 个概念拆解 / 一个常见错误）"
                "+ objective + 1-3 个 practice（含参考答案）+ assessment。",
                "调用 write_lesson 成功后立即停止：不要重写已完成章节，"
                "不要开始下一课，不要写长篇总结。",
                "需要前文细节时调用 read_course，不要依赖对话历史。",
            ]
        )
        return "\n".join(lines)


def lesson_exists(course_id: str, lesson_id: str, root=None) -> bool:
    return lesson_file(course_id, lesson_id, root).exists()
