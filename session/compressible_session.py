from collections.abc import Sequence
from .protocol import Session
from ..schemas import Message, Usage
from ..infra import COMPRESS
from .policies import CompressionPolicy, CompressionContext
from .compressors import Compressor


class CompressibleSession:
    def __init__(
        self,
        session: Session,
        policy: CompressionPolicy,
        compressor: Compressor,
        *,
        auto_compress: bool = True,
    ):
        self._inner = session
        self._auto_compress = auto_compress
        self._policy = policy
        self._compressor = compressor

    async def get(self, limit: int | None = None) -> list[Message]:
        return await self._inner.get(limit=limit)

    async def add(self, items: Sequence[Message], usage: Usage | None = None):
        await self._inner.add(items, usage)

        items = await self._inner.get()
        if self._auto_compress:
            if self._policy.hit(CompressionContext(items=items, last_usage=usage)):
                await self.compress()

    async def pop(self) -> Message | None:
        return await self._inner.pop()

    async def clear(self):
        await self._inner.clear()

    async def close(self):
        await self._inner.close()

    async def compress(self):
        original = await self._inner.get()
        compressed = await self._compressor.compress(original)
        COMPRESS.add(
            1, {"messages.before": len(original), "messages.after": len(compressed)}
        )
        await self._inner.clear()
        await self._inner.add(compressed)
