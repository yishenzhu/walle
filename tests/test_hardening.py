"""Session/Channel 最小硬化回归：忙拒、取消终态、max_turns、detach fencing、call 超时、帧上限。"""

import asyncio
import json

import pytest

from ..channel.cli import CLIConnection, CLIServer
from ..channel.cli.server import MAX_FRAME
from ..conf import ApprovalConfig, ApprovalDecision, ToolConfig
from ..core import Session, SessionBusyError, SessionManager
from ..messages import build_history
from ..spec import ApprovalRequest, DeltaEnd, MessageEndEvent, UserInput
from .conftest import FakeChannel


def make_session(session_id: str, db_path: str) -> Session:
    return Session(
        session_id=session_id,
        history=build_history("memory", db_path, session_id),
        tool_config=ToolConfig(
            approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
        ),
        transport=FakeChannel(),
    )


async def test_handle_rejects_when_busy(tmp_path):
    s = make_session("busy", str(tmp_path / "s.db"))
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(_content: str) -> None:
        started.set()
        await release.wait()

    s._execute = slow  # type: ignore[method-assign]
    first = asyncio.create_task(s.handle(UserInput(content="one")))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert s.state == "running"
        with pytest.raises(SessionBusyError):
            await s.handle(UserInput(content="two"))
        release.set()
        await first
        assert s.state == "idle"
    finally:
        release.set()
        await s.close()


async def test_cancel_emits_terminal(tmp_path):
    s = make_session("cancel", str(tmp_path / "s.db"))
    started = asyncio.Event()

    async def slow_run(*_args, **_kwargs):
        started.set()
        await asyncio.sleep(10)

    s._agent_runner.run = slow_run  # type: ignore[method-assign]
    task = asyncio.create_task(s.handle(UserInput(content="hi")))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert await s.cancel(reason="test_cancel") is True
        await task
        ends = [e for e in s.transport.events if isinstance(e, DeltaEnd)]
        assert ends and ends[-1].reason == "test_cancel"
        assert s.state == "idle"
    finally:
        await s.close()


async def test_max_turns_emits_terminal(tmp_path):
    s = make_session("max", str(tmp_path / "s.db"))
    try:
        await s._bus.emit(
            MessageEndEvent(output=None, session_id=s.id, reason="max_turns")
        )
        assert isinstance(s.transport.events[-1], DeltaEnd)
        assert s.transport.events[-1].reason == "max_turns"
    finally:
        await s.close()


async def test_detach_ignores_stale_transport(tmp_path):
    s = make_session("fence", str(tmp_path / "s.db"))
    old = s.transport
    new = FakeChannel()
    try:
        s.attach(new)
        assert s.detach(old) is False
        assert s.transport is new
        assert s.detach(new) is True
    finally:
        await s.close()


async def test_attach_takeover_cancels_old_run(tmp_path):
    """新连接接管：旧 run 取消，终态发给新连接。"""
    manager = SessionManager(
        tool_config=ToolConfig(
            approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
        ),
        storage="memory",
        path=str(tmp_path / "s.db"),
    )
    server = CLIServer(sessions=manager, host="127.0.0.1", port=0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]

    async def connect(hello):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write((json.dumps(hello) + "\n").encode())
        await writer.drain()
        return reader, writer

    async def read_frame(reader):
        return json.loads(await asyncio.wait_for(reader.readline(), 2))

    r1, w1 = await connect({"type": "hello", "chat_id": "takeover"})
    r2 = w2 = None
    try:
        await read_frame(r1)  # welcome
        session = manager.get("takeover")
        started = asyncio.Event()

        async def slow_run(*_args, **_kwargs):
            started.set()
            await asyncio.sleep(10)

        session._agent_runner.run = slow_run  # type: ignore[method-assign]
        w1.write((json.dumps({"type": "input", "content": "hi"}) + "\n").encode())
        await w1.drain()
        await asyncio.wait_for(started.wait(), 1)

        r2, w2 = await connect(
            {"type": "hello", "chat_id": "takeover", "attach": True}
        )
        assert (await read_frame(r2))["type"] == "welcome"

        reason = None
        for _ in range(3):
            msg = await read_frame(r2)
            if msg.get("type") == "notify":
                reason = msg["data"].get("reason")
                if reason:
                    break
        assert reason == "session reattached"
    finally:
        for writer in (w1, w2):
            if writer is not None:
                writer.close()
                await writer.wait_closed()
        await server.stop()
        await manager.close()


async def test_input_failure_reports_code():
    async def handle(reader, writer):
        conn = CLIConnection("busy", reader, writer)

        async def on_input(_content: str) -> None:
            raise SessionBusyError("busy")

        try:
            await conn.run(on_input)
        finally:
            conn.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, limit=MAX_FRAME)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write((json.dumps({"type": "input", "content": "x"}) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), 2)
        msg = json.loads(line)
        assert msg["data"]["type"] == "error"
        assert msg["data"]["code"] == "session_busy"
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()


async def test_call_times_out_without_reply():
    done = asyncio.Event()

    async def handle(reader, writer):
        conn = CLIConnection("timeout", reader, writer, call_timeout=0.05)
        try:
            with pytest.raises(TimeoutError):
                await conn.call(
                    ApprovalRequest(tool_name="bash", arguments={}, tool_call_id="tc1")
                )
        finally:
            done.set()
            conn.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, limit=MAX_FRAME)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await asyncio.wait_for(done.wait(), 2)
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()


async def test_oversized_frame_gets_terminal():
    async def handle(reader, writer):
        conn = CLIConnection("big", reader, writer)
        try:
            await conn.run(lambda _: asyncio.sleep(0))
        finally:
            conn.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, limit=MAX_FRAME)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(b"x" * (MAX_FRAME + 1024) + b"\n")
        await writer.drain()

        reason = None
        for _ in range(3):
            line = await asyncio.wait_for(reader.readline(), 2)
            if not line:
                break
            msg = json.loads(line)
            if msg.get("type") == "notify":
                reason = msg["data"].get("reason")
                break
        assert reason == "frame_too_large"
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()
