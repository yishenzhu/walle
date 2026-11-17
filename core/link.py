"""会话连接端点：transport、断线补发缓冲与交互调用。"""

from __future__ import annotations

import asyncio

from ..spec import Channel, NotificationUnion, RequestUnion

# 断连补发缓冲：按条数和字符数双上限，长时间 turn 也不至于丢开头
BUFFER_LIMIT = 2000
BUFFER_CHARS = 256 * 1024


class SessionClosedError(RuntimeError):
    code = "session_closed"


class SessionLink:
    """一条会话连接的运行时状态：transport / 断线缓冲 / 重放。"""

    def __init__(self, session_id: str, transport: Channel | None = None) -> None:
        self._session_id = session_id
        self._transport = transport
        self._attached = asyncio.Event()
        if transport is not None:
            self._attached.set()
        # (seq, notification)：seq 单调递增，供重连按断点回放
        self._pending: list[tuple[int, NotificationUnion]] = []
        self._pending_chars = 0
        self._seq = 0
        self._replaying = False
        self._closed = False

    @property
    def transport(self) -> Channel | None:
        return self._transport

    @property
    def attached(self) -> bool:
        return self._transport is not None

    @property
    def seq(self) -> int:
        """已发出的最后一条通知序号。"""
        return self._seq

    async def notify(self, notification: NotificationUnion) -> None:
        """缓冲当前 turn 的通知并推给当前 transport。"""
        self._seq += 1
        size = len(repr(notification))
        self._pending.append((self._seq, notification))
        self._pending_chars += size
        while self._pending and (
            len(self._pending) > BUFFER_LIMIT or self._pending_chars > BUFFER_CHARS
        ):
            _, dropped = self._pending.pop(0)
            self._pending_chars -= len(repr(dropped))
        if self._replaying:
            return
        if self._transport is not None:
            await self._transport.notify(notification)

    async def call(self, request: RequestUnion):
        """向当前连接发起交互；断连时等待重连，连接失败则重试。"""
        while True:
            if self._closed:
                raise SessionClosedError(f"session '{self._session_id}' is closed")
            transport = self._transport
            if transport is not None:
                try:
                    return await transport.call(request)
                except ConnectionError:
                    if self._closed:
                        raise
                    if self._transport is transport:
                        self._attached.clear()
            await self._attached.wait()

    async def replay(self, transport: Channel, last_seq: int | None = None) -> None:
        """补发断连期间的通知；给了 last_seq 就只发它之后的部分。"""
        pending, self._pending = self._pending, []
        self._pending_chars = 0
        self._replaying = True
        try:
            for seq, notification in pending:
                if last_seq is not None and seq <= last_seq:
                    continue
                await transport.notify(notification)
        finally:
            self._replaying = False
            rest, self._pending = self._pending, []
            self._pending_chars = 0
            for _, notification in rest:
                await transport.notify(notification)

    def attach(self, transport: Channel) -> None:
        """绑定新 transport（重连/接管）：换 channel 端点。"""
        if self._closed:
            raise SessionClosedError(f"session '{self._session_id}' is closed")
        self._transport = transport
        self._attached.set()

    def detach(self, transport: Channel | None = None) -> bool:
        """解除 transport；给定旧端点时只解除当前端点，防止误伤新连接。"""
        if transport is not None and transport is not self._transport:
            return False
        self._transport = None
        self._attached.clear()
        return True

    def clear_pending(self) -> None:
        """清掉当前 turn 的实时通知缓冲。"""
        self._pending.clear()
        self._pending_chars = 0

    def close(self) -> None:
        """会话销毁：唤醒等待重连的调用方。"""
        self._closed = True
        self._attached.set()
        self._pending.clear()
