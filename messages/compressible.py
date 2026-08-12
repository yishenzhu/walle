from collections.abc import Sequence
from .protocol import Messages
from ..schemas import Message, Usage
from ..infra import COMPRESS
from .policies import CompressionPolicy, CompressionContext
from .compressors import Compressor


class CompressibleMessages:
    def __init__(
        self,
        messages: Messages,
        policy: CompressionPolicy,
        compressor: Compressor,
        *,
        auto_compress: bool = True,
    ):
        self._messages = messages
        self._auto_compress = auto_compress
        self._policy = policy
        self._compressor = compressor

    async def get(self, limit: int | None = None) -> list[Message]:
        return await self._messages.get(limit=limit)

    async def add(self, items: Sequence[Message], usage: Usage | None = None):
        await self._messages.add(items, usage)

        items = await self._messages.get()
        if self._auto_compress:
            if self._policy.hit(CompressionContext(items=items, last_usage=usage)):
                await self.compress()

    async def pop(self) -> Message | None:
        return await self._messages.pop()

    async def clear(self):
        await self._messages.clear()

    async def close(self):
        await self._messages.close()

    async def compress(self):
        original = await self._messages.get()
        compressed = await self._compressor.compress(original)
        COMPRESS.add(
            1, {"messages.before": len(original), "messages.after": len(compressed)}
        )
        await self._messages.clear()
        await self._messages.add(compressed)
