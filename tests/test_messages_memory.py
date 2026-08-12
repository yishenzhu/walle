"""InMemoryMessages 测试。"""
import pytest

from ..schemas import UserMessage, SystemMessage, Usage
from ..messages import InMemoryMessages


@pytest.fixture
def messages():
    return InMemoryMessages()


class TestInMemoryMessages:
    async def test_add_and_get(self, messages):
        msgs = [UserMessage(content="hello"), SystemMessage(content="sys")]
        await messages.add(msgs)
        result = await messages.get()
        assert len(result) == 2
        assert result[0].content == "hello"
        assert result[1].content == "sys"

    async def test_get_with_limit(self, messages):
        for i in range(5):
            await messages.add([UserMessage(content=f"msg{i}")])
        result = await messages.get(limit=2)
        assert len(result) == 2
        assert result[0].content == "msg3"
        assert result[1].content == "msg4"

    async def test_get_returns_copy(self, messages):
        await messages.add([UserMessage(content="hello")])
        result1 = await messages.get()
        result1.append(UserMessage(content="injected"))
        result2 = await messages.get()
        assert len(result2) == 1

    async def test_pop(self, messages):
        await messages.add([UserMessage(content="first"), UserMessage(content="second")])
        popped = await messages.pop()
        assert popped.content == "first"
        remaining = await messages.get()
        assert len(remaining) == 1
        assert remaining[0].content == "second"

    async def test_pop_empty(self, messages):
        popped = await messages.pop()
        assert popped is None

    async def test_clear(self, messages):
        await messages.add([UserMessage(content="hello")])
        await messages.clear()
        result = await messages.get()
        assert len(result) == 0

    async def test_add_empty_list(self, messages):
        await messages.add([])
        result = await messages.get()
        assert len(result) == 0

    async def test_add_with_usage(self, messages):
        usage = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        await messages.add([UserMessage(content="hi")], usage=usage)
        result = await messages.get()
        assert len(result) == 1
