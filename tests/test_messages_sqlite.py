"""SQLiteMessages 测试。"""
import pytest

from ..schemas import UserMessage, SystemMessage, AssistantMessage
from ..messages import SQLiteMessages


@pytest.fixture
def messages(tmp_path):
    return SQLiteMessages(db_path=str(tmp_path / "test.db"), session_id="test")


class TestSQLiteMessages:
    async def test_add_and_get(self, messages):
        msgs = [UserMessage(content="hello"), SystemMessage(content="sys")]
        await messages.add(msgs)
        result = await messages.get()
        assert len(result) == 2
        assert result[0].role == "user"
        assert result[0].content == "hello"
        assert result[1].role == "system"

    async def test_get_with_limit(self, messages):
        for i in range(5):
            await messages.add([UserMessage(content=f"msg{i}")])
        result = await messages.get(limit=2)
        assert len(result) == 2
        assert result[0].content == "msg0"
        assert result[1].content == "msg1"

    async def test_pop(self, messages):
        await messages.add([UserMessage(content="first"), UserMessage(content="second")])
        popped = await messages.pop()
        assert popped.content == "first"
        remaining = await messages.get()
        assert len(remaining) == 1

    async def test_pop_empty(self, messages):
        popped = await messages.pop()
        assert popped is None

    async def test_clear(self, messages):
        await messages.add([UserMessage(content="hello")])
        await messages.clear()
        result = await messages.get()
        assert len(result) == 0

    async def test_messages_isolation(self, tmp_path):
        db = str(tmp_path / "multi.db")
        s1 = SQLiteMessages(db_path=db, session_id="s1")
        s2 = SQLiteMessages(db_path=db, session_id="s2")

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

    async def test_assistant_message_with_tool_calls(self, messages):
        msg = AssistantMessage(
            content="thinking...",
            tool_calls=[{"id": "tc1", "function": {"name": "bash", "arguments": "{}"}}],
        )
        await messages.add([msg])
        result = await messages.get()
        assert len(result) == 1
        assert result[0].role == "assistant"
        assert result[0].tool_calls is not None

    async def test_close_and_reopen(self, tmp_path):
        db = str(tmp_path / "reopen.db")
        s1 = SQLiteMessages(db_path=db, session_id="default")
        await s1.add([UserMessage(content="persisted")])
        await s1.close()

        s2 = SQLiteMessages(db_path=db, session_id="default")
        result = await s2.get()
        assert len(result) == 1
        assert result[0].content == "persisted"
        await s2.close()
