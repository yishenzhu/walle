"""课程工具测试：create_course / write_lesson / read_course。"""

import pytest

from ..infra import tool_context
from ..learning import create_course, read_course, write_lesson
from ..learning import store as course_store
from .conftest import make_session


@pytest.fixture
def courses(tmp_path, monkeypatch):
    root = tmp_path / "courses"
    monkeypatch.setattr(course_store, "courses_root", lambda path=None: root)
    return root


@pytest.fixture
def context(tmp_path):
    token = tool_context.set(make_session(cwd=str(tmp_path / "workspace")))
    try:
        yield
    finally:
        tool_context.reset(token)


async def test_create_course_and_write_lesson(courses, context):
    result = await create_course(
        "傅里叶变换",
        goal="理解频域",
        lessons=["时域与频域", "傅里叶级数"],
    )
    assert not isinstance(result, str)
    assert result.type == "course"
    assert result.lesson_count == 2

    lesson = await write_lesson(
        result.course_id,
        "时域与频域",
        "# 时域与频域\n\n{{canvas:wave}}",
    )
    assert not isinstance(lesson, str)
    assert lesson.type == "lesson"
    assert lesson.order == 1
    path = courses / result.course_id / "lessons" / f"{lesson.lesson_id}.md"
    assert "{{canvas:wave}}" in path.read_text(encoding="utf-8")


async def test_create_course_requires_title(courses, context):
    result = await create_course("   ")
    assert isinstance(result, str)
    assert result.startswith("Error:")


async def test_write_lesson_unknown_course(courses, context):
    result = await write_lesson("missing", "课", "content")
    assert isinstance(result, str)
    assert "not found" in result


async def test_read_course_uses_session_context(courses, tmp_path):
    created = await create_course("课程", goal="目标", lessons=["第一课"])
    lesson = await write_lesson(created.course_id, "第一课", "# 正文\n细节")
    assert not isinstance(lesson, str)

    token = tool_context.set(
        make_session(
            cwd=str(tmp_path / "workspace"),
            metadata={"course_id": created.course_id, "lesson_id": lesson.lesson_id},
        )
    )
    try:
        content = await read_course()
        assert "# 正文" in content
        assert "细节" in content
        outline = await read_course(outline=True)
        assert "第一课" in outline
        assert "(current)" in outline
    finally:
        tool_context.reset(token)


async def test_read_course_without_context(courses, context):
    result = await read_course()
    assert isinstance(result, str)
    assert "没有课程上下文" in result
