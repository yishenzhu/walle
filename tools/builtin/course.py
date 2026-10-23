"""课程工具：创建课程大纲、写入课节 md。"""

import time
from typing import Literal

from pydantic import BaseModel

from ...infra import tool_context
from ...infra.course_store import create_course as create_course_entry
from ...infra.course_store import save_course
from ...infra.course_store import write_lesson as write_lesson_entry


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

    Use this when the user wants to learn a topic and needs a structured plan.
    `lessons` is an ordered list of lesson titles; write each lesson afterwards
    with write_lesson. Pass `course_id` to update an existing course.
    """
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

    Markdown supports headings, lists, code and LaTeX. To embed an interactive
    demo, first call canvas with the same course_id, then put `{{canvas:<id>}}`
    on its own line in the Markdown.
    """
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
