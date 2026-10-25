"""课程工具：创建课程大纲、写入课节 md。"""

import time
from typing import Literal

from pydantic import BaseModel

from ..infra import tool_context
from ..tools.builtin.canvas import CanvasResult, write_canvas
from .store import canvas_dir, load_course, read_lesson, save_course
from .store import create_course as create_course_entry
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


class CourseCanvasResult(CanvasResult):
    course_id: str


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


async def course_canvas(
    title: str,
    html: str,
    course_id: str,
    canvas_id: str | None = None,
) -> CourseCanvasResult | str:
    """把交互画布写入课程包，供课节 Markdown 用 {{canvas:<id>}} 嵌入。

    html 必须是完整 HTML 页面，可内联 CSS/JS 或引用外部 CDN；更新已有画布时
    传回上次返回的 canvas_id。先建画布，再调用 write_lesson 写入含占位符的课节。
    """
    if load_course(course_id) is None:
        return f"Error: course 不存在: {course_id}"
    result = write_canvas(title, html, canvas_id, canvas_dir(course_id))
    if isinstance(result, str):
        return result
    return CourseCanvasResult(**result.model_dump(), course_id=course_id)


async def read_course(
    course_id: str | None = None,
    lesson_id: str | None = None,
    outline: bool = False,
) -> str:
    """Read the current course outline or one lesson's full Markdown.

    In a course Q&A session, course_id/lesson_id default to the current page.
    Without `outline`, the current lesson is returned; set outline=True to list
    the course structure. Use this before answering material-specific questions.
    """
    ctx = tool_context.get()
    metadata = ctx.metadata if ctx is not None else {}
    cid = course_id or metadata.get("course_id")
    lid = None if outline else lesson_id or metadata.get("lesson_id")
    if not cid:
        return "Error: 当前会话没有课程上下文，请提供 course_id"
    course = load_course(cid)
    if course is None:
        return f"Error: course 不存在: {cid}"

    if lid:
        lesson = read_lesson(cid, lid)
        if lesson is None:
            return f"Error: lesson 不存在: {lid}"
        return (
            f"# {course.get('title')} / {lesson.get('title')}\n\n"
            f"{lesson.get('markdown') or ''}"
        )

    lines = [f"# {course.get('title')}"]
    if course.get("goal"):
        lines.append(course["goal"])
    lines.append("Lessons:")
    current = metadata.get("lesson_id")
    for item in course.get("lessons") or []:
        marker = " (current)" if item.get("id") == current else ""
        lines.append(
            f"- {item.get('order') or '?'}. {item.get('title')} "
            f"[{item.get('id')}]{marker}"
        )
    return "\n".join(lines)
