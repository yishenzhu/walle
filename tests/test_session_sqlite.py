"""SQLiteSession 测试。"""
import pytest

from ..schemas import UserMessage, SystemMessage, AssistantMessage
from ..session.sqlite import SQLiteSession


@pytest.fixture
def session(tmp_path):
    return SQLiteSession(db_path=str(tmp_path / "test.db"), session_id="test")


class TestSQLiteSession:
    async def test_add_and_get(self, session):
        msgs = [UserMessage(content="hello"), SystemMessage(content="sys")]
        await session.add(msgs)
        result = await session.get()
        assert len(result) == 2
        assert result[0].role == "user"
        assert result[0].content == "hello"
        assert result[1].role == "system"

    async def test_get_with_limit(self, session):
        for i in range(5):
            await session.add([UserMessage(content=f"msg{i}")])
        result = await session.get(limit=2)
        assert len(result) == 2
        assert result[0].content == "msg0"
        assert result[1].content == "msg1"

    async def test_pop(self, session):
        await session.add([UserMessage(content="first"), UserMessage(content="second")])
        popped = await session.pop()
        assert popped.content == "first"
        remaining = await session.get()
        assert len(remaining) == 1

    async def test_pop_empty(self, session):
        popped = await session.pop()
        assert popped is None

    async def test_clear(self, session):
        await session.add([UserMessage(content="hello")])
        await session.clear()
        result = await session.get()
        assert len(result) == 0

    async def test_session_isolation(self, tmp_path):
        db = str(tmp_path / "multi.db")
        s1 = SQLiteSession(db_path=db, session_id="s1")
        s2 = SQLiteSession(db_path=db, session_id="s2")

        await s1.add([UserMessage(content="from-s1")])
        await s2.add([UserMessage(content="from-s2")])

        r1 = await s1.get()
        r2 = await s2.get()
        assert len(r1) == 1
        assert len(r2) == 1
        assert r1[0].content == "from-s1"
        assert r2[0].content == "from-s2"

        await s1.close()
        await s2.close()

    async def test_assistant_message_with_tool_calls(self, session):
        msg = AssistantMessage(
            content="thinking...",
            tool_calls=[{"id": "tc1", "function": {"name": "bash", "arguments": "{}"}}],
        )
        await session.add([msg])
        result = await session.get()
        assert len(result) == 1
        assert result[0].role == "assistant"
        assert result[0].tool_calls is not None

    async def test_close_and_reopen(self, tmp_path):
        db = str(tmp_path / "reopen.db")
        s1 = SQLiteSession(db_path=db, session_id="default")
        await s1.add([UserMessage(content="persisted")])
        await s1.close()

        s2 = SQLiteSession(db_path=db, session_id="default")
        result = await s2.get()
        assert len(result) == 1
        assert result[0].content == "persisted"
        await s2.close()
