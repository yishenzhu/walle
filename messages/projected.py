"""投影视图：把底层会话历史投影成模型可见形态。"""

from collections.abc import Sequence

from ..spec import Message, Messages, Projection, ProjectionStore, Usage


class ProjectedMessages(Projection):
    """包装底层 Messages，读取时投影成「切点后原文」。"""

    def __init__(
        self,
        messages: Messages,
        projection_store: ProjectionStore | None = None,
    ):
        self._messages = messages
        self._store = projection_store
        self._cut: int | None = None  # 移出模型视野的前 N 条；None = 不投影
        self._restored = False

    @property
    def underlying(self) -> Messages:
        """被包装的底层存储（窗口查询 / 读原文用）。"""
        return self._messages

    # ── 持久化（惰性恢复，幂等） ─────────────────────────
    async def _ensure_restored(self) -> None:
        if self._restored or self._store is None:
            return
        self._restored = True
        cut = await self._store.load()
        if cut is None:
            return
        # 恢复合法性：切点不得超出当前底层消息数（clear 后失效则丢弃）
        total = await self._messages.count()
        if 0 <= cut <= total:
            self._cut = cut

    # ── 投影控制（new_window 工具用） ────────────────────
    async def set_projection(self, cut: int) -> None:
        """设切点：前 cut 条消息移出模型视野（纯截断，底层原文保留）。"""
        if cut < 0:
            raise ValueError("cut must be >= 0")
        await self._ensure_restored()
        self._cut = cut
        if self._store is not None:
            await self._store.save(cut)

    async def clear_projection(self) -> None:
        """清除投影，恢复底层原始视图。"""
        await self._ensure_restored()
        self._cut = None
        if self._store is not None:
            await self._store.clear()

    async def get_projection(self) -> int | None:
        """读当前切点；无投影返回 None。"""
        await self._ensure_restored()
        return self._cut

    # ── 模型视图 ────────────────────────────────────────
    async def get(self) -> list[Message]:
        await self._ensure_restored()
        messages = await self._messages.get()
        if self._cut is not None and self._cut < len(messages):
            messages = messages[self._cut :]
        # 返回副本：调用方修改不影响投影内部/底层状态
        return list(messages)

    # ── 透传底层存储 ────────────────────────────────────
    async def add(self, items: Sequence[Message], usage: Usage | None = None):
        await self._ensure_restored()
        await self._messages.add(items, usage)

    async def query(self, offset: int = 0, limit: int = 20) -> list[Message]:
        # 窗口查询永远查底层原文（不经投影）：history 回源工具读折叠前细节
        return await self._messages.query(offset, limit)

    async def search(self, query: str = "", limit: int = 20) -> list[tuple[int, Message]]:
        # 检索永远查底层原文（不经投影），返回 (绝对序号, 消息) 倒序
        return await self._messages.search(query, limit)

    async def count(self) -> int:
        return await self._messages.count()

    async def pop(self) -> Message | None:
        # 纯透传：切点 = 「当前底层全量的前 N 条」，pop 改变全量后仍自洽
        return await self._messages.pop()

    async def clear(self):
        await self._messages.clear()
        # 底层清空后切点失去意义，一并重置（含持久化投影）
        await self.clear_projection()

    async def close(self):
        await self._messages.close()
