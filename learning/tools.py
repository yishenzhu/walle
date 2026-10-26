"""Course tools: create courses, write lessons, and read course material."""

import time
from typing import Literal

from pydantic import BaseModel

from ..infra import tool_context
from .store import create_course as create_course_entry
from .store import load_course, outline_lines, read_lesson, save_course
from .store import write_lesson as write_lesson_entry


class CourseResult(BaseModel):
    type: Literal["course"] = "course"
    course_id: str
    title: str
    lesson_count: int
    version: int


class LessonResult(BaseModel):
    type: Literal["lesson"] = "lesson"
    course_id: str
    lesson_id: str
    title: str
    order: int
    version: int


async def create_course(
    title: str,
    goal: str = "",
    lessons: list[str] | None = None,
    course_id: str | None = None,
) -> CourseResult | str:
    """Create or update a course package with an ordered lesson outline.
    Use when the user wants a structured learning plan; `lessons` is an ordered
    list of titles. Pass `course_id` to update an existing course."""
    try:
        course = create_course_entry(
            title=title,
            goal=goal,
            lessons=lessons,
            course_id=course_id,
        )
    except ValueError as exc:
        return f"Error: {exc}"

    ctx = tool_context.get()
    if ctx is not None:
        course["session_id"] = ctx.session_id
        if ctx.cwd:
            course["workspace"] = str(ctx.cwd)
        save_course(course)

    return CourseResult(
        course_id=course["id"],
        title=course["title"],
        lesson_count=len(course.get("lessons") or []),
        version=time.time_ns(),
    )


async def write_lesson(
    course_id: str,
    title: str,
    markdown: str,
    order: int | None = None,
    lesson_id: str | None = None,
) -> LessonResult | str:
    """Write one lesson's Markdown into a course package.
    Supports Markdown, LaTeX and `{{canvas:<id>}}`; call `canvas` first,
    then put its id on its own line."""
    try:
        entry = write_lesson_entry(
            course_id=course_id,
            title=title,
            markdown=markdown,
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
        version=time.time_ns(),
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
