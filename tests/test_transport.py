"""JSONLineConn 传输层回归：坏帧跳过、超长帧、关闭语义。"""

import asyncio

import pytest

from ..channel.transport import FrameTooLargeError, JSONLineConn


class FakeWriter:
    def __init__(self, fail: Exception | None = None):
        self.frames: list[bytes] = []
        self.closed = False
        self._fail = fail

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        if self._fail is not None:
            raise self._fail

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


def make_link(reader: asyncio.StreamReader, writer=None) -> JSONLineConn:
    return JSONLineConn(reader, writer or FakeWriter())


async def test_recv_skips_bad_frames():
    reader = asyncio.StreamReader()
    reader.feed_data(b'not-json\n[]\n{"type":"input.create","content":"hi"}\n')
    reader.feed_eof()

    link = make_link(reader)
    assert await link.recv() == {"type": "input.create", "content": "hi"}
    assert await link.recv() is None


async def test_recv_skips_blank_lines():
    reader = asyncio.StreamReader()
    reader.feed_data(b'\n   \n{"type":"ok"}\n')
    reader.feed_eof()

    assert await make_link(reader).recv() == {"type": "ok"}


async def test_recv_oversized_frame_raises():
    reader = asyncio.StreamReader(limit=32)
    reader.feed_data(b"x" * 64 + b"\n")
    reader.feed_eof()

    with pytest.raises(FrameTooLargeError):
        await make_link(reader).recv()


async def test_send_writes_one_json_line():
    writer = FakeWriter()
    link = make_link(asyncio.StreamReader(), writer)

    await link.send({"type": "ping"})

    assert writer.frames == [b'{"type": "ping"}\n']


async def test_try_send_failure_closes():
    writer = FakeWriter(fail=TimeoutError())
    link = make_link(asyncio.StreamReader(), writer)

    assert await link.try_send({"type": "notify"}) is False
    assert writer.closed
    with pytest.raises(ConnectionError):
        await link.send({"type": "notify"})


async def test_close_is_idempotent():
    writer = FakeWriter()
    link = make_link(asyncio.StreamReader(), writer)

    link.close()
    link.close()
    await link.wait_closed()

    assert writer.closed
