"""学习扩展：课程工具 + 课程答疑上下文（按会话 metadata 注入）。"""

from typing import Any

from ..infra import ExtensionAPI, Tool
from .store import load_course, read_lesson
from .tools import course_canvas, create_course, read_course, write_lesson


def course_instructions(metadata: dict[str, Any]) -> str | None:
    """按会话 metadata 生成课程答疑提示；未指定课程时返回 None。"""
    course_id = metadata.get("course_id")
    if not course_id:
        return None
    lesson_id = metadata.get("lesson_id")
    course = load_course(course_id)
    if course is None:
        return f"Course context: {course_id} (course not found)"
    lines = [f"Course: {course.get('title') or course_id}"]
    if course.get("goal"):
        lines.append(f"Goal: {course['goal']}")
    lines.append("Lessons:")
    for item in course.get("lessons") or []:
        marker = " (current)" if item.get("id") == lesson_id else ""
        lines.append(
            f"- {item.get('order') or '?'}. {item.get('title')} "
            f"[{item.get('id')}]{marker}"
        )
    if lesson_id:
        lesson = read_lesson(course_id, lesson_id)
        if lesson is not None:
            lines.append(
                f"Current lesson: {lesson.get('title')} "
                f"[{lesson_id}]; use read_course to read it."
            )
    lines.append("Answer as the course tutor. Use read_course for lesson details.")
    return "\n".join(lines)


async def learning_extension(api: ExtensionAPI) -> None:
    """注册课程工具与动态指令。"""
    for fn in (create_course, write_lesson, course_canvas, read_course):
        api.register_tool(Tool.from_function(fn))
    api.register_instructions(course_instructions)
