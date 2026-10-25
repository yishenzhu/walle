"""Session/Channel 最小硬化回归：忙拒、取消终态、max_turns、detach fencing、call 超时、帧上限。"""

import asyncio
import json

import pytest

from ..channel import SessionConn, SessionServer
from ..channel.server import MAX_FRAME
from ..conf import ApprovalConfig, ApprovalDecision, ToolConfig
from ..core import Session, SessionBusyError, SessionManager
from ..messages import build_history
from ..spec import (
    ApprovalRequest,
    Delta,
    DeltaEnd,
    Error,
    MessageEndEvent,
    UserInput,
)
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


async def test_session_buffers_events_until_attach(tmp_path):
    """断连期间的通知进入缓冲，重连后补发。"""
    s = make_session("buffer", str(tmp_path / "s.db"))
    s.detach()
    await s.notify(Delta(delta="partial"))
    channel = FakeChannel()
    s.attach(channel)
    await s.replay(channel)
    try:
        assert channel.events == [Delta(delta="partial")]
        assert s.context.channel is s
    finally:
        await s.close()


async def test_session_call_waits_for_attach(tmp_path):
    """断连时的审批请求等待重连，而不是直接失败。"""
    s = make_session("wait-call", str(tmp_path / "s.db"))
    s.detach()
    task = asyncio.create_task(
        s.call(
            ApprovalRequest(tool_name="bash", arguments={}, tool_call_id="tc1")
        )
    )
    await asyncio.sleep(0)
    assert not task.done()
    channel = FakeChannel()
    s.attach(channel)
    try:
        response = await asyncio.wait_for(task, 1)
        assert response.approved is True
    finally:
        await s.close()


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


async def test_run_error_goes_to_current_transport(tmp_path):
    """run 跨连接存活时，异常必须回报当前 transport 而不是旧连接。"""
    s = make_session("error-route", str(tmp_path / "s.db"))
    old = FakeChannel()
    new = FakeChannel()
    s.attach(old)
    started = asyncio.Event()
    release = asyncio.Event()

    async def boom(_content: str) -> None:
        started.set()
        await release.wait()
        raise RuntimeError("boom")

    s._execute = boom  # type: ignore[method-assign]
    task = s.submit(UserInput(content="hi"))
    try:
        await asyncio.wait_for(started.wait(), 1)
        s.attach(new)
        release.set()
        await task
        assert any(isinstance(event, Error) for event in new.events)
        assert not any(isinstance(event, Error) for event in old.events)
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


async def test_attach_takeover_keeps_old_run(tmp_path):
    """新连接接管：旧 run 继续跑，新连接接收后续事件。"""
    manager = SessionManager(
        tool_config=ToolConfig(
            approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
        ),
        storage="memory",
        path=str(tmp_path / "s.db"),
    )
    server = SessionServer(sessions=manager, host="127.0.0.1", port=0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]

    async def connect(hello):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write((json.dumps(hello) + "\n").encode())
        await writer.drain()
        return reader, writer

    async def read_frame(reader):
        return json.loads(await asyncio.wait_for(reader.readline(), 2))

    r1, w1 = await connect({"type": "hello", "session_id": "takeover"})
    r2 = w2 = None
    try:
        await read_frame(r1)  # welcome
        session = manager.get("takeover")
        started = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def slow_run(*_args, **_kwargs):
            started.set()
            await release.wait()
            finished.set()

        session._agent_runner.run = slow_run  # type: ignore[method-assign]
        w1.write((json.dumps({"type": "input.create", "content": "hi"}) + "\n").encode())
        await w1.drain()
        await asyncio.wait_for(started.wait(), 1)

        r2, w2 = await connect(
            {"type": "hello", "session_id": "takeover", "attach": True}
        )
        assert (await read_frame(r2))["type"] == "welcome"

        old_msg = await read_frame(r1)
        assert old_msg["type"] == "notify"
        assert old_msg["data"]["code"] == "session_taken_over"
        assert session.state == "running"

        release.set()
        await asyncio.wait_for(finished.wait(), 2)
        for _ in range(50):
            if session.state == "idle":
                break
            await asyncio.sleep(0.02)
        assert session.state == "idle"
    finally:
        for writer in (w1, w2):
            if writer is not None:
                writer.close()
                await writer.wait_closed()
        await server.stop()
        await manager.close()


async def test_attach_keeps_run(tmp_path):
    """重连接管：旧 run 继续执行，新连接接收后续事件。"""
    manager = SessionManager(
        tool_config=ToolConfig(
            approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
        ),
        storage="memory",
        path=str(tmp_path / "s.db"),
    )
    server = SessionServer(sessions=manager, host="127.0.0.1", port=0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]

    async def connect(hello):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write((json.dumps(hello) + "\n").encode())
        await writer.drain()
        return reader, writer

    async def read_frame(reader):
        return json.loads(await asyncio.wait_for(reader.readline(), 2))

    r1, w1 = await connect(
        {"type": "hello", "session_id": "learning-resume", "agent": "learning"}
    )
    r2 = w2 = None
    try:
        await read_frame(r1)  # welcome
        session = manager.get("learning-resume")
        started = asyncio.Event()
        finished = asyncio.Event()

        async def slow_run(*_args, **_kwargs):
            started.set()
            await asyncio.sleep(0.2)
            finished.set()

        session._agent_runner.run = slow_run  # type: ignore[method-assign]
        w1.write((json.dumps({"type": "input.create", "content": "生成课程"}) + "\n").encode())
        await w1.drain()
        await asyncio.wait_for(started.wait(), 1)

        r2, w2 = await connect(
            {
                "type": "hello",
                "session_id": "learning-resume",
                "attach": True,
                "agent": "learning",
            }
        )
        assert (await read_frame(r2))["type"] == "welcome"

        # 旧连接必须收到明确的接管原因，前端据此停止自动重连，
        # 否则两个标签页会互相接管、无限重连。
        old_msg = await read_frame(r1)
        assert old_msg["type"] == "notify"
        assert old_msg["data"]["code"] == "session_taken_over"

        await asyncio.wait_for(finished.wait(), 2)
        for _ in range(50):
            if session.state == "idle":
                break
            await asyncio.sleep(0.02)
        assert session.state == "idle"
    finally:
        for writer in (w1, w2):
            if writer is not None:
                writer.close()
                await writer.wait_closed()
        await server.stop()
        await manager.close()


async def test_attach_switches_agent_and_keeps_history(tmp_path):
    """重连场景：attach 现有会话时按 hello 里的 agent 切配置，历史不丢。"""
    manager = SessionManager(
        tool_config=ToolConfig(
            approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
        ),
        storage="memory",
        path=str(tmp_path / "s.db"),
    )
    server = SessionServer(sessions=manager, host="127.0.0.1", port=0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]

    async def connect(hello):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write((json.dumps(hello) + "\n").encode())
        await writer.drain()
        return reader, writer

    r1, w1 = await connect(
        {"type": "hello", "session_id": "switch-agent", "agent": "learning"}
    )
    r2 = w2 = None
    try:
        msg = json.loads(await asyncio.wait_for(r1.readline(), 2))
        assert msg["type"] == "welcome"
        session = manager.get("switch-agent")
        assert session.agent_name == "learning"

        r2, w2 = await connect(
            {
                "type": "hello",
                "session_id": "switch-agent",
                "attach": True,
                "agent": "default",
            }
        )
        msg = json.loads(await asyncio.wait_for(r2.readline(), 2))
        assert msg["type"] == "welcome"
        assert session.agent_name == "default"
    finally:
        for writer in (w1, w2):
            if writer is not None:
                writer.close()
                await writer.wait_closed()
        await server.stop()
        await manager.close()


async def test_server_switches_agent_on_same_connection(tmp_path):
    """学习模式/课程切换走 session.* 帧：同一连接内切换，不触发 reattach。"""
    manager = SessionManager(
        tool_config=ToolConfig(
            approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
        ),
        storage="memory",
        path=str(tmp_path / "s.db"),
    )
    server = SessionServer(sessions=manager, host="127.0.0.1", port=0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]

    async def connect(hello):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write((json.dumps(hello) + "\n").encode())
        await writer.drain()
        return reader, writer

    reader, writer = await connect(
        {"type": "hello", "session_id": "live-switch", "agent": "learning"}
    )
    try:
        msg = json.loads(await asyncio.wait_for(reader.readline(), 2))
        assert msg["type"] == "welcome"
        session = manager.get("live-switch")
        assert session.agent_name == "learning"

        writer.write(
            (
                json.dumps(
                    {
                        "type": "session.set_agent",
                        "agent": "default",
                    }
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        for _ in range(100):
            if session.agent_name == "default":
                break
            await asyncio.sleep(0.02)
        assert session.agent_name == "default"

        writer.write(
            (
                json.dumps(
                    {
                        "type": "session.set_metadata",
                        "values": {"course_id": "course-a", "lesson_id": "lesson-a"},
                    }
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        for _ in range(100):
            if session.metadata.get("course_id") == "course-a":
                break
            await asyncio.sleep(0.02)
        assert session.metadata == {"course_id": "course-a", "lesson_id": "lesson-a"}
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
        await manager.close()


async def test_inbound_failure_reports_code():
    async def handle(reader, writer):
        conn = SessionConn("busy", reader, writer)

        async def on_inbound(_frame: dict) -> None:
            raise SessionBusyError("busy")

        try:
            await conn.run(on_inbound)
        finally:
            conn.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, limit=MAX_FRAME)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write((json.dumps({"type": "input.create", "content": "x"}) + "\n").encode())
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
        conn = SessionConn("timeout", reader, writer, call_timeout=0.05)
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
        conn = SessionConn("big", reader, writer)

        async def on_inbound(_frame: dict) -> None:
            pass

        try:
            await conn.run(on_inbound)
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
