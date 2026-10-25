"""学习扩展：课程上下文按会话 meta 注入，课程工具作为扩展声明注册。"""

from ..core import ExtensionManager
from ..learning import course_instructions, learning_extension
from ..learning import store as course_store


async def test_course_instructions_follows_meta(tmp_path, monkeypatch):
    root = tmp_path / "courses"
    monkeypatch.setattr(course_store, "courses_root", lambda path=None: root)
    course = course_store.create_course(
        "测试课程", goal="目标", lessons=["第一课"], root=root
    )
    lesson = course_store.write_lesson(
        course["id"], "第一课", "# 正文", root=root
    )

    text = course_instructions(
        {"course_id": course["id"], "lesson_id": lesson["id"]}
    )
    assert "测试课程" in text
    assert "第一课" in text
    assert "(current)" in text

    assert "(current)" not in course_instructions({"course_id": course["id"]})
    assert course_instructions({}) is None


async def test_learning_extension_registers_tools_and_instructions():
    mgr = ExtensionManager()
    mgr.add("learning", learning_extension)
    await mgr.load()

    extension = mgr.extensions[0]
    names = {tool.name for tool in extension.tools}
    assert {"create_course", "write_lesson", "course_canvas", "read_course"} <= names
    assert extension.instructions
