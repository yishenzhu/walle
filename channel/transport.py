"""JSON-line 帧传输：CLI server/client 与 Web bridge 共用的唯一实现。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 通道默认连接参数（服务端监听 / 客户端连接共用同一值，避免漂移）
HOST = "127.0.0.1"
PORT = 8899

# 单帧上限与写超时：慢消费者 / 超大帧都不能拖死 Agent 循环
MAX_FRAME = 1 << 20
SEND_TIMEOUT = 10.0


class FrameTooLargeError(Exception):
    """单帧超过读取上限（asyncio.StreamReader.readline 的 limit）。"""


class JSONLineConn:
    """一条 JSON-line 连接：每帧一行，UTF-8 JSON。"""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        send_timeout: float = SEND_TIMEOUT,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._send_timeout = send_timeout
        self._closed = False

    async def send(self, msg: dict[str, Any]) -> None:
        """写一帧；连接已关闭抛 ConnectionError，写超时抛 TimeoutError。"""
        if self._closed:
            raise ConnectionError("connection closed")
        self._writer.write(json.dumps(msg).encode() + b"\n")
        await asyncio.wait_for(self._writer.drain(), self._send_timeout)

    async def try_send(self, msg: dict[str, Any]) -> bool:
        """尽力发送：失败即关闭连接并返回 False（通知类消息用）。"""
        try:
            await self.send(msg)
        except (TimeoutError, ConnectionError, OSError):
            self.close()
            return False
        return True

    async def recv(self) -> dict[str, Any] | None:
        """读一帧：EOF 返回 None；坏 JSON 跳过；超长帧抛 FrameTooLargeError。"""
        while not self._closed:
            try:
                line = await self._reader.readline()
            except ValueError as exc:
                # readline 超过 StreamReader limit（limit=MAX_FRAME）：拒绝并交由上层收尾
                raise FrameTooLargeError(f"frame exceeds {MAX_FRAME} bytes") from exc
            if not line:
                return None
            text = line.decode(errors="replace").strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("ignoring malformed json frame")
                continue
            if isinstance(msg, dict):
                return msg
            logger.warning("ignoring non-object json frame")
        return None

    def close(self) -> None:
        """幂等关闭写端（不等待对端）。"""
        if self._closed:
            return
        self._closed = True
        self._writer.close()

    async def wait_closed(self) -> None:
        """等待写端关闭；对端已断开时吞掉 OSError。"""
        try:
            await self._writer.wait_closed()
        except (OSError, ConnectionError):
            pass
