"""投影视图：把底层会话历史投影成模型可见形态。

原始消息在底层存储全量保留，投影只影响读取：位于切点之前的消息被折叠
为一条摘要 SystemMessage，切点及之后的消息原样返回。不设投影时（切点为
None）行为等同底层存储。

切点是「折叠前 N 条消息」的位置语义，不依赖底层 id/序号，故 InMemory 与
SQLite 行为一致；如需稳定地址/按轮折叠属后续扩展。

投影状态（cut / summary）默认进程内存级；持久化不在本模块职责。
"""

from collections.abc import Sequence

from ..schemas import Message, SystemMessage, Usage
from ..schemas.protocols import Messages, Projection


class ProjectedMessages(Projection):
    """包装底层 Messages，读取时投影成「摘要 + 切点后原文」。

    实现 Projection 协议（underlying + set_projection）：压缩扩展经协议
    操作投影，不依赖本具体类。Runner 无感知：它只调用 get()，拿到的是
    投影结果。底层原文永不清除。

    get() 语义：先对底层全量做切点折叠，再按 limit 取前 N 条。limit 由本类
    统一处理，不依赖底层存储的实现差异（InMemory 与 SQLite 的 limit 语义
    原本不对称）。
    """

    def __init__(self, messages: Messages):
        self._messages = messages
        self._cut: int | None = None  # 折叠前 N 条；None = 不投影
        self._summary: str | None = None

    @property
    def underlying(self) -> Messages:
        """被包装的底层存储（窗口查询 / 读原文用）。"""
        return self._messages

    # ── 投影控制（扩展用） ──────────────────────────────
    async def set_projection(self, cut: int, summary: str) -> None:
        """设切点与摘要：前 cut 条消息在投影中折叠为 summary。"""
        if cut < 0:
            raise ValueError("cut must be >= 0")
        self._cut = cut
        self._summary = summary

    async def clear_projection(self) -> None:
        """清除投影，恢复底层原始视图。"""
        self._cut = None
        self._summary = None

    # ── 模型视图 ────────────────────────────────────────
    async def get(self, limit: int | None = None) -> list[Message]:
        messages = await self._messages.get()
        # 先投影（折叠切点前为摘要），后应用 limit（取前 N 条）
        if self._cut is not None and self._summary is not None:
            if self._cut <= 0:
                messages = [SystemMessage(content=self._summary)]
            elif self._cut < len(messages):
                tail = messages[self._cut :]
                messages = [SystemMessage(content=self._summary)] + tail
            # cut >= len(messages)：不折叠，保持全量
        if limit is not None:
            messages = messages[:limit]
        # 返回副本：调用方修改不影响投影内部/底层状态
        return list(messages)

    # ── 透传底层存储 ────────────────────────────────────
    async def add(self, items: Sequence[Message], usage: Usage | None = None):
        await self._messages.add(items, usage)

    async def pop(self) -> Message | None:
        # 纯透传：切点 = 「当前底层全量的前 N 条」，pop 改变全量后仍自洽
        return await self._messages.pop()

    async def clear(self):
        await self._messages.clear()
        # 底层清空后切点失去意义，一并重置
        await self.clear_projection()

    async def close(self):
        await self._messages.close()
