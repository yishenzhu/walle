"""学习插件：课程工具 + 动态课程提示（按会话 metadata 注入）。"""

from ..core.prompt import Prompts
from ..infra import SessionToolTable
from ..learning import course_instructions, learning_plugin
from ..learning import store as course_store
from ..plugin import PluginHost
from ..spec import AssembleContext, PromptService, ToolService


async def test_course_context_follows_meta(tmp_path, monkeypatch):
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


async def test_learning_plugin_registers_tools_and_prompt_section():
    host = PluginHost()
    await host.root.register(ToolService, SessionToolTable())
    await host.root.register(PromptService, Prompts())
    try:
        await host.launch([learning_plugin])
        plugin = host.entries[0]
        assert plugin.state.value == "active"

        names = {tool.name for tool in host.root.get(ToolService).all_tools()}
        assert {"create_course", "write_lesson", "read_course"} <= names

        assembly = await host.root.get(PromptService).assemble(
            AssembleContext(agent="tutor")
        )
        assert assembly.sections == []  # 没有课程元数据时不注入
    finally:
        await host.dispose()
