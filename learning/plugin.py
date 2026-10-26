"""学习插件：课程工具 + 课程动态提示（按会话 metadata 注入）。"""

from typing import Any

from ..infra import Tool
from ..plugin import plugin
from ..spec import (
    AssembleContext,
    PromptOrder,
    PromptSection,
    PromptService,
    ToolService,
)
from .store import load_course, outline_lines, read_lesson
from .tools import create_course, read_course, write_lesson


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
    lines.extend(outline_lines(course, lesson_id))
    if lesson_id:
        lesson = read_lesson(course_id, lesson_id)
        if lesson is not None:
            lines.append(
                f"Current lesson: {lesson.get('title')} "
                f"[{lesson_id}]; use read_course to read it."
            )
    lines.append("Answer as the course tutor. Use read_course for lesson details.")
    return "\n".join(lines)


def _course_section(context: AssembleContext) -> str:
    """PromptService 段：课程上下文随会话 metadata 变化。"""
    return course_instructions(context.metadata) or ""


@plugin(name="learning", inject=(ToolService, PromptService))
async def learning_plugin(ctx, config) -> None:
    """注册课程工具与课程动态提示段。"""
    tools = ctx.get(ToolService)
    for fn in (create_course, write_lesson, read_course):
        tools.add(Tool.from_function(fn))
    ctx.get(PromptService).section(
        PromptSection(
            name="learning:course",
            order=PromptOrder.RUNTIME,
            text=_course_section,
        )
    )


PLUGIN = learning_plugin

# Web 进程按需加载的 Web 插件（惰性导入，避免 agent 进程加载 FastAPI）
WEB_PLUGINS = ["walle.learning.web:PLUGIN"]
