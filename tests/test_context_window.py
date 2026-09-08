"""窗口管理测试：切点持久化 + new_window 工具（模型主动硬切窗口）。

窗口管理无压缩扩展：模型维护 .agent/note.md 后调 new_window 推进投影切点，
旧轮移出模型视野（底层原文全量保留，history 可回源）。切点持久化保证
重启后折叠不失效。
"""

from ..infra import ToolContext, tool_context
from ..messages import (
    InMemoryMessages,
    ProjectedMessages,
    SQLiteMessages,
    SQLiteProjectionStore,
)
from ..schemas import UserMessage


class TestProjectionPersistence:
    async def test_restore_after_reopen(self, tmp_path):
        """切点持久化，重建实例后懒恢复。"""
        db = str(tmp_path / "ctx.db")
        raw1 = SQLiteMessages(db_path=db, session_id="s1")
        h1 = ProjectedMessages(
            raw1, projection_store=SQLiteProjectionStore(db_path=db, session_id="s1")
        )
        await h1.add([UserMessage(content=f"u{i}") for i in range(6)])
        await h1.set_projection(4)
        await h1.close()

        # 模拟重启：全新实例读同一 db，首次 get 触发懒恢复
        raw2 = SQLiteMessages(db_path=db, session_id="s1")
        h2 = ProjectedMessages(
            raw2, projection_store=SQLiteProjectionStore(db_path=db, session_id="s1")
        )
        projected = await h2.get()
        assert [m.content for m in projected] == ["u4", "u5"]
        assert await h2.projection() == 4
        await h2.close()

    async def test_clear_projection_persists(self, tmp_path):
        db = str(tmp_path / "clear.db")
        h1 = ProjectedMessages(
            SQLiteMessages(db_path=db, session_id="s1"),
            projection_store=SQLiteProjectionStore(db_path=db, session_id="s1"),
        )
        await h1.add([UserMessage(content="u0"), UserMessage(content="u1")])
        await h1.set_projection(1)
        await h1.clear_projection()
        await h1.close()

        h2 = ProjectedMessages(
            SQLiteMessages(db_path=db, session_id="s1"),
            projection_store=SQLiteProjectionStore(db_path=db, session_id="s1"),
        )
        projected = await h2.get()
        assert [m.content for m in projected] == ["u0", "u1"]
        await h2.close()

    async def test_projection_state_readable(self, tmp_path):
        h = ProjectedMessages(InMemoryMessages())
        await h.add([UserMessage(content="u0")])
        assert await h.projection() is None
        await h.set_projection(1)
        assert await h.projection() == 1


class TestNewWindowTool:
    """new_window 工具：模型主动硬切窗口（保留当前轮，笔记在 .agent/note.md）。"""

    async def _projected(self, with_turns=4):
        proj = ProjectedMessages(InMemoryMessages())
        for i in range(with_turns):
            await proj.add([UserMessage(content=f"u{i}")])
        return proj

    async def test_hard_cuts_to_current_turn(self):
        from ..messages.tool import new_window

        proj = await self._projected()
        token = tool_context.set(ToolContext(history=proj))
        try:
            out = await new_window()
        finally:
            tool_context.reset(token)
        assert "window reset" in out
        assert "1 messages remain" in out
        visible = await proj.get()
        # 硬切后只剩当前轮（最后一条 UserMessage）
        assert [m.content for m in visible] == ["u3"]

    async def test_no_notes_no_problem(self):
        """工具只做窗口切换，不强制笔记文件（模型自律由提示词引导）。"""
        from ..messages.tool import new_window

        proj = ProjectedMessages(InMemoryMessages())
        for i in range(3):
            await proj.add([UserMessage(content=f"u{i}")])
        token = tool_context.set(ToolContext(history=proj))
        try:
            out = await new_window()
        finally:
            tool_context.reset(token)
        assert "window reset" in out

    async def test_single_turn_no_reset(self):
        """只有一轮时没有可折叠的旧窗口，工具提示已是最新边界。"""
        from ..messages.tool import new_window

        proj = ProjectedMessages(InMemoryMessages())
        await proj.add([UserMessage(content="u0")])
        token = tool_context.set(ToolContext(history=proj))
        try:
            out = await new_window()
        finally:
            tool_context.reset(token)
        assert "window reset" not in out

    async def test_no_context_returns_error(self):
        from ..messages.tool import new_window

        token = tool_context.set(ToolContext())
        try:
            assert "Error" in await new_window()
        finally:
            tool_context.reset(token)

    async def test_non_projected_history_errors(self):
        """裸存储（无 Projection）无法硬切，工具报错而非崩溃。"""
        from ..messages.tool import new_window

        token = tool_context.set(ToolContext(history=InMemoryMessages()))
        try:
            assert "Error" in await new_window()
        finally:
            tool_context.reset(token)

    async def test_fold_only_advances(self):
        """重复调用不回退切点：第二次硬切不再前进则提示已是最新。"""
        from ..messages.tool import new_window

        proj = await self._projected(with_turns=6)
        token = tool_context.set(ToolContext(history=proj))
        try:
            await new_window()
            visible = await proj.get()
            assert [m.content for m in visible] == ["u5"]
            # 再次调用：已在最新边界
            out2 = await new_window()
            assert "window reset" not in out2
        finally:
            tool_context.reset(token)
