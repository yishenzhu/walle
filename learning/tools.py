"""Course tools: create courses, write lessons, and read course material."""

import time
from typing import Literal

from pydantic import BaseModel

from ..infra import tool_context
from .store import (
    confirm_required,
    load_course,
    mark_course,
    next_pending_lesson,
    outline_lines,
    read_lesson,
    save_course,
)
from .store import create_course as create_course_entry
from .store import write_lesson as write_lesson_entry


class Practice(BaseModel):
    """一个可判定的练习：题目 + 参考答案（+ 可选的判定标准）。"""

    prompt: str
    answer: str
    check: str = ""
    type: Literal["task", "code", "quiz"] = "task"


class CourseResult(BaseModel):
    type: Literal["course"] = "course"
    course_id: str
    title: str
    lesson_count: int
    cache_buster: int


class LessonResult(BaseModel):
    type: Literal["lesson"] = "lesson"
    course_id: str
    lesson_id: str
    title: str
    order: int
    cache_buster: int


async def create_course(
    title: str,
    goal: str = "",
    lessons: list[str] | None = None,
    course_id: str | None = None,
    can_do: list[str] | None = None,
    learner_level: str = "",
    time_budget: str = "",
) -> CourseResult | str:
    """Create or update a course package: outcome + ordered lesson outline.

    Design backwards. `can_do` are 1-5 observable abilities the learner must be
    able to demonstrate after the course ("能用 X 完成 Y，满足 Z"); each one must
    be verified by at least one lesson practice. `goal` is a one-line summary.
    `learner_level` / `time_budget` calibrate difficulty and scope.
    Pass `course_id` to update an existing course."""
    previous = load_course(course_id) if course_id else None
    try:
        course = create_course_entry(
            title=title,
            goal=goal,
            lessons=lessons,
            course_id=course_id,
            can_do=can_do,
            learner_level=learner_level,
            time_budget=time_budget,
        )
    except ValueError as exc:
        return f"Error: {exc}"

    ctx = tool_context.get()
    if ctx is not None:
        course["session_id"] = ctx.session_id
        if ctx.cwd:
            course["workspace"] = str(ctx.cwd)
        save_course(course)

    # 新大纲先等用户确认，避免一头扎进去写一整门课；
    # 正在生成的课程改大纲不打断，交给驱动继续
    was_generating = bool(previous and previous.get("status") == "generating")
    if (
        confirm_required()
        and not was_generating
        and next_pending_lesson(course) is not None
    ):
        mark_course(course["id"], "awaiting_outline")
        course["status"] = "awaiting_outline"

    return CourseResult(
        course_id=course["id"],
        title=course["title"],
        lesson_count=len(course.get("lessons") or []),
        cache_buster=time.time_ns(),
    )


async def write_lesson(
    course_id: str,
    title: str,
    markdown: str,
    objective: str,
    practice: list[Practice],
    assessment: str = "",
    minutes: int | None = None,
    order: int | None = None,
    lesson_id: str | None = None,
) -> LessonResult | str:
    """Write one lesson: body + a verifiable objective + at least one practice.

    Lesson body must contain: a concrete scenario, a runnable worked example,
    1-3 core concepts, and at least one common mistake. `objective` states what
    the learner can do afterwards. `practice` is 1-5 exercises, each with a
    reference `answer` (and optional `check`). `assessment` is the self-check
    the learner uses to judge mastery. Supports Markdown, LaTeX and
    `{{canvas:<id>}}` on its own line."""
    course = load_course(course_id)
    if (
        course is not None
        and course.get("status") == "awaiting_outline"
        and confirm_required()
    ):
        return (
            "Error: 大纲尚未确认，不能写正文。"
            "请让用户确认大纲；确认后系统会按章节自动生成。"
        )
    try:
        entry = write_lesson_entry(
            course_id=course_id,
            title=title,
            markdown=markdown,
            objective=objective,
            practice=[
                item.model_dump()
                if isinstance(item, Practice)
                else Practice.model_validate(item).model_dump()
                for item in practice
            ],
            assessment=assessment,
            minutes=minutes,
            order=order,
            lesson_id=lesson_id,
        )
    except ValueError as exc:
        return f"Error: {exc}"

    return LessonResult(
        course_id=course_id,
        lesson_id=entry["id"],
        title=entry["title"],
        order=entry.get("order") or 0,
        cache_buster=time.time_ns(),
    )


async def read_course(
    course_id: str | None = None,
    lesson_id: str | None = None,
    outline: bool = False,
) -> str:
    """Read the current course outline or one lesson's full Markdown.
    course_id/lesson_id default to the current page; `outline=True` lists
    the course structure. Use before answering material questions."""
    ctx = tool_context.get()
    metadata = ctx.metadata if ctx is not None else {}
    course_key = course_id or metadata.get("course_id")
    lesson_key = None if outline else lesson_id or metadata.get("lesson_id")
    if not course_key:
        return "Error: 当前会话没有课程上下文，请提供 course_id"
    course = load_course(course_key)
    if course is None:
        return f"Error: course 不存在: {course_key}"

    if lesson_key:
        lesson = read_lesson(course_key, lesson_key)
        if lesson is None:
            return f"Error: lesson 不存在: {lesson_key}"
        return (
            f"# {course.get('title')} / {lesson.get('title')}\n\n"
            f"{lesson.get('markdown') or ''}"
        )

    lines = [f"# {course.get('title')}"]
    if course.get("goal"):
        lines.append(course["goal"])
    lines.extend(outline_lines(course, metadata.get("lesson_id")))
    return "\n".join(lines)
