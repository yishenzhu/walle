"""InMemorySession 测试。"""
import pytest

from ..schemas import UserMessage, SystemMessage, Usage
from ..session.memory import InMemorySession


@pytest.fixture
def session():
    return InMemorySession()


class TestInMemorySession:
    async def test_add_and_get(self, session):
        msgs = [UserMessage(content="hello"), SystemMessage(content="sys")]
        await session.add(msgs)
        result = await session.get()
        assert len(result) == 2
        assert result[0].content == "hello"
        assert result[1].content == "sys"

    async def test_get_with_limit(self, session):
        for i in range(5):
            await session.add([UserMessage(content=f"msg{i}")])
        result = await session.get(limit=2)
        assert len(result) == 2
        assert result[0].content == "msg3"
        assert result[1].content == "msg4"

    async def test_get_returns_copy(self, session):
        await session.add([UserMessage(content="hello")])
        result1 = await session.get()
        result1.append(UserMessage(content="injected"))
        result2 = await session.get()
        assert len(result2) == 1

    async def test_pop(self, session):
        await session.add([UserMessage(content="first"), UserMessage(content="second")])
        popped = await session.pop()
        assert popped.content == "first"
        remaining = await session.get()
        assert len(remaining) == 1
        assert remaining[0].content == "second"

    async def test_pop_empty(self, session):
        popped = await session.pop()
        assert popped is None

    async def test_clear(self, session):
        await session.add([UserMessage(content="hello")])
        await session.clear()
        result = await session.get()
        assert len(result) == 0

    async def test_add_empty_list(self, session):
        await session.add([])
        result = await session.get()
        assert len(result) == 0

    async def test_add_with_usage(self, session):
        usage = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        await session.add([UserMessage(content="hi")], usage=usage)
        result = await session.get()
        assert len(result) == 1
