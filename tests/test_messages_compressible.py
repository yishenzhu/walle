"""CompressibleMessages 测试。"""
import pytest

from ..schemas import UserMessage, Usage, Message
from ..messages import (
    InMemoryMessages,
    CompressibleMessages,
    PromptLimitPolicy,
    CompressionContext,
)


class FakeCompressor:
    """记录调用并将前半部分消息替换为一条摘要。"""

    def __init__(self):
        self.called = False
        self.received: list[Message] = []

    async def compress(self, items: list[Message]) -> list[Message]:
        self.called = True
        self.received = list(items)
        if len(items) <= 1:
            return items
        return [UserMessage(content="[summary]")] + list(items[len(items) // 2:])


@pytest.fixture
def inner():
    return InMemoryMessages()


@pytest.fixture
def compressor():
    return FakeCompressor()


@pytest.fixture
def messages(inner, compressor):
    return CompressibleMessages(
        messages=inner,
        policy=PromptLimitPolicy(limit=100),
        compressor=compressor,
    )


class TestCompressibleMessages:
    async def test_add_below_limit_no_compress(self, messages, compressor):
        await messages.add(
            [UserMessage(content="hi")],
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        assert compressor.called is False

    async def test_add_above_limit_triggers_compress(self, messages, compressor):
        await messages.add(
            [UserMessage(content="hi")],
            usage=Usage(prompt_tokens=200, completion_tokens=10, total_tokens=210),
        )
        assert compressor.called is True

    async def test_auto_compress_disabled(self, inner, compressor):
        messages = CompressibleMessages(
            messages=inner,
            policy=PromptLimitPolicy(limit=1),
            compressor=compressor,
            auto_compress=False,
        )
        await messages.add(
            [UserMessage(content="hi")],
            usage=Usage(prompt_tokens=1000, completion_tokens=10, total_tokens=1010),
        )
        assert compressor.called is False

    async def test_manual_compress(self, messages, compressor):
        for i in range(4):
            await messages.add([UserMessage(content=f"msg{i}")])
        await messages.compress()
        assert compressor.called is True
        result = await messages.get()
        assert result[0].content == "[summary]"

    async def test_delegates_pop(self, messages):
        await messages.add([UserMessage(content="first"), UserMessage(content="second")])
        popped = await messages.pop()
        assert popped.content == "first"

    async def test_delegates_clear(self, messages):
        await messages.add([UserMessage(content="hi")])
        await messages.clear()
        result = await messages.get()
        assert len(result) == 0
