"""课程生成驱动器：一次只推进一课，失败退避，超限暂停。"""

from __future__ import annotations

import asyncio
import logging

from ..spec import Scheduler
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


class CourseDriver:
    """按课程状态推进生成：空闲时提交「只写一课」的 run，写完再推进下一课。"""

    def __init__(
        self,
        scheduler: Scheduler,
        session_id: str,
        root=None,
        tick: float = 60.0,
        stall: float = 900.0,
        max_attempts: int = 3,
    ):
        self._scheduler = scheduler
        self._session_id = session_id
        self._root = root
        self._tick = tick
        self._stall = stall
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
                course = mark_course(item["id"], "generating", root=self._root, last_error=None)
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

            prompt = self._prompt(course, pending)
            task = self._scheduler.submit_background(prompt)
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

    def _prompt(self, course: dict, lesson: dict) -> str:
        written, total = written_progress(course, self._root)
        return (
            f"只完成课程《{course.get('title')}》（course_id={course['id']}）的"
            f"第 {lesson.get('order')} 课《{lesson.get('title')}》。\n"
            "要求：正文（场景 / 可运行示例 / 1-3 个概念拆解 / 一个常见错误）"
            "+ objective + 1-3 个 practice（含参考答案）+ assessment。\n"
            "调用 write_lesson 成功后立即停止，不要开始下一课，也不要写长篇总结。\n"
            f"当前进度：{written}/{total}。"
        )


def lesson_exists(course_id: str, lesson_id: str, root=None) -> bool:
    return lesson_file(course_id, lesson_id, root).exists()
