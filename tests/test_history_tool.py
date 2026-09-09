"""history 工具与存储层窗口/检索测试。

覆盖：SQLite/InMemory 的 query/search/count；history 工具渲染与过滤；
折叠后仍能经 history 查回被折叠的原文（无损回源）。
"""
import pytest

from ..infra import tool_context
from ..messages import (
    InMemoryMessages,
    ProjectedMessages,
    SQLiteMessages,
)
from ..messages.tool import history
from ..schemas import UserMessage

from .conftest import make_session


def make_storage(kind, tmp_path, session_id="s1"):
    if kind == "memory":
        return InMemoryMessages()
    return SQLiteMessages(db_path=str(tmp_path / "t.db"), session_id=session_id)


def sample_messages():
    from ..schemas import AssistantMessage, ToolMessage

    return [
        UserMessage(content="把超时改成 30 秒"),
        UserMessage(content="deploy 脚本失败，日志在 /tmp/deploy.log"),
        AssistantMessage(
            content="我用 bash 查了日志",
            tool_calls=[
                {"id": "tc1", "function": {"name": "bash", "arguments": "{}"}}
            ],
        ),
        ToolMessage(content="line 42: connection refused", tool_call_id="tc1"),
        UserMessage(content="确认重试上限是 3 次"),
    ]


async def with_history_tool(messages, fn, *args, **kwargs):
    """在指定 messages 存储下调用 history 工具（注入会话视图）。"""
    token = tool_context.set(make_session(history=messages))
    try:
        return await fn(*args, **kwargs)
    finally:
        tool_context.reset(token)


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
class TestStorageSearchQuery:
    async def test_query_window(self, kind, tmp_path):
        store = make_storage(kind, tmp_path)
        await store.add(sample_messages())
        win = await store.query(offset=1, limit=2)
        assert win[0].content == "deploy 脚本失败，日志在 /tmp/deploy.log"
        assert win[1].role == "assistant"  # 第三条是 assistant 消息

    async def test_query_beyond_end_empty(self, kind, tmp_path):
        store = make_storage(kind, tmp_path)
        await store.add(sample_messages())
        assert await store.query(offset=100, limit=5) == []

    async def test_count(self, kind, tmp_path):
        store = make_storage(kind, tmp_path)
        await store.add(sample_messages())
        assert await store.count() == 5

    async def test_search_matches_content(self, kind, tmp_path):
        store = make_storage(kind, tmp_path)
        await store.add(sample_messages())
        matches = await store.search("重试")
        assert len(matches) == 1
        assert "重试上限" in matches[0][1].content
        assert matches[0][0] == 4  # 命中条是会话内第 4 条（含 0 基序号）

    async def test_search_empty_returns_recent(self, kind, tmp_path):
        store = make_storage(kind, tmp_path)
        await store.add(sample_messages())
        matches = await store.search("", limit=2)
        assert len(matches) == 2
        assert matches[0][1].content == "确认重试上限是 3 次"  # 最新在前
        assert matches[0][0] == 4  # 且带绝对序号

    async def test_search_limit(self, kind, tmp_path):
        store = make_storage(kind, tmp_path)
        for i in range(10):
            await store.add([UserMessage(content=f"item {i}")])
        matches = await store.search("item", limit=3)
        assert len(matches) == 3
        assert matches[0][1].content == "item 9"
        assert matches[0][0] == 9  # 绝对序号与内容对应


async def test_search_scope_isolation(tmp_path):
    """search 只查本 session_id 的消息。"""
    db = str(tmp_path / "iso.db")
    s1 = SQLiteMessages(db_path=db, session_id="s1")
    s2 = SQLiteMessages(db_path=db, session_id="s2")
    await s1.add([UserMessage(content="secret-key-xyz")])
    await s2.add([UserMessage(content="other")])
    assert await s1.search("secret-key-xyz") != []
    assert await s2.search("secret-key-xyz") == []
    await s1.close()
    await s2.close()


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
class TestHistoryTool:
    async def _projected(self, kind, tmp_path):
        proj = ProjectedMessages(make_storage(kind, tmp_path))
        await proj.add(sample_messages())
        return proj

    async def test_query_filters_and_renders(self, kind, tmp_path):
        proj = await self._projected(kind, tmp_path)
        out = await with_history_tool(proj, history, "重试")
        assert "确认重试上限是 3 次" in out
        assert "[4]" in out  # 命中带绝对序号，可据此翻附近窗口

    async def test_search_hit_index_leads_to_around_window(self, kind, tmp_path):
        """搜到命中序号后，用 offset 查看该条附近的上下文窗口。"""
        proj = await self._projected(kind, tmp_path)
        out = await with_history_tool(proj, history, "deploy 脚本失败")
        assert "[1]" in out  # 命中在第 1 条
        # 用命中序号往前翻：看 [0,2) 的上下文
        around = await with_history_tool(proj, history, offset=0, limit=3)
        assert "messages 0..2 of 5" in around
        assert "把超时改成 30 秒" in around  # 命中前一条
        assert "deploy 脚本失败" in around

    async def test_empty_query_returns_recent(self, kind, tmp_path):
        proj = await self._projected(kind, tmp_path)
        out = await with_history_tool(proj, history, limit=2)
        assert "2 matched" in out
        assert "确认重试上限是 3 次" in out  # 最新在前

    async def test_no_match(self, kind, tmp_path):
        proj = await self._projected(kind, tmp_path)
        out = await with_history_tool(proj, history, "不存在的词xyz")
        assert "0 matched" in out

    async def test_offset_window_ascending_with_indexes(self, kind, tmp_path):
        proj = await self._projected(kind, tmp_path)
        # offset=1：从第 1 条起正序看 2 条，带绝对序号
        out = await with_history_tool(proj, history, offset=1, limit=2)
        assert "messages 1..2 of 5" in out
        assert "deploy 脚本失败" in out  # 原第 1 条
        assert "我用 bash 查了日志" in out  # 原第 2 条（assistant）
        assert "[1]" in out and "[2]" in out
        # 升序：deploy 在 assistant 之前
        assert out.index("deploy") < out.index("bash 查了日志")

    async def test_offset_beyond_end_empty(self, kind, tmp_path):
        proj = await self._projected(kind, tmp_path)
        out = await with_history_tool(proj, history, offset=100)
        assert "no messages at index 100" in out

    async def test_recovers_folded_details(self, kind, tmp_path):
        """硬切（new_window 推进切点）后模型可见区收窄，history 仍查回原文。"""
        proj = ProjectedMessages(make_storage(kind, tmp_path))
        # 7 条历史：切点推进到 5，模型视野只剩最近 2 条，早期细节移出
        await proj.add(
            sample_messages()
            + [UserMessage(content="第 6 条"), UserMessage(content="第 7 条")]
        )
        await proj.set_projection(5)

        # 模型可见：只剩切点后原文（纯截断，无摘要占位）
        visible = await proj.get()
        assert [m.content for m in visible] == ["第 6 条", "第 7 条"]

        # history 工具：仍能查回被移出视野的原始细节
        out = await with_history_tool(proj, history, "deploy 脚本失败")
        assert "/tmp/deploy.log" in out


async def test_history_tool_no_context_returns_error():
    out = await with_history_tool(None, history, "x")
    assert "Error" in out
