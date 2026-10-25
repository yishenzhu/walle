"""会话服务端/通道连接回归测试。

覆盖读循环并发、命令帧与审批回复验证。"""

import asyncio
import json

import pytest

from ..channel import SessionConn, SessionServer
from ..channel.server import CALL_TIMEOUT
from ..conf import ApprovalConfig, ApprovalDecision, ToolConfig
from ..core import SessionManager
from ..spec import (
    ApprovalRequest,
    ApprovalResponse,
    AssistantMessage,
    MessageDeltaEvent,
    ToolMessage,
    UserMessage,
)
from ..tools.approval import ChannelApprover


async def test_conn_run_processes_reply_while_input_in_flight():
    """读循环不被 input 占住：agent 运行中等待的审批 reply 必须被读到。"""
    results: dict = {}

    async def handle_client(reader, writer):
        conn = SessionConn("test-conn", reader, writer)

        async def work(content):
            # 模拟 agent run：后台发起审批 call 并等待 reply
            rsp = await conn.call(
                ApprovalRequest(tool_name="bash", arguments={}, tool_call_id="tc1")
            )
            results["approval"] = rsp

        async def on_inbound(frame):
            if frame.get("type") == "input.create":
                asyncio.create_task(work(frame.get("content") or ""))

        try:
            await conn.run(on_inbound)
        finally:
            # 须显式关服务端侧连接：3.12 起 server.wait_closed() 会等所有
            # 连接结束，服务端 transport 不关则 _active_count 不归零 → 挂起。
            # （生产 SessionServer._handle_conn 同样在 finally 里关 writer）
            writer.close()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write((json.dumps({"type": "input.create", "content": "hi"}) + "\n").encode())
        await writer.drain()

        # 服务端 agent（on_inbound 调度的任务）应发出审批 call 帧
        line = await asyncio.wait_for(reader.readline(), timeout=2)
        msg = json.loads(line)
        assert msg["type"] == "call"
        assert msg["data"]["type"] == "approval"

        # 回 reply：修复前读循环被 input 处理占住，reply 无人读取 → call 永不完成
        writer.write(
            (
                json.dumps(
                    {"type": "reply", "id": msg["id"], "data": {"approved": True}}
                )
                + "\n"
            ).encode()
        )
        await writer.drain()

        for _ in range(100):
            if "approval" in results:
                break
            await asyncio.sleep(0.02)
        assert results.get("approval") == {"approved": True}
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()


async def test_close_unblocks_run_without_peer_eof():
    """对端仍在线时 close() 也必须结束读循环。"""

    class StalledLink:
        async def recv(self):
            await asyncio.Event().wait()

        async def try_send(self, msg):
            return True

        async def send(self, msg):
            pass

        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def on_inbound(frame):
        pass

    conn = SessionConn("stalled", None, None, link=StalledLink())
    task = asyncio.create_task(conn.run(on_inbound))
    await asyncio.sleep(0)
    conn.close()
    await asyncio.wait_for(task, 1)


async def test_conn_run_notifies_error_on_inbound_failure():
    """on_inbound 抛异常：run 捕获后向客户端发 error 帧。

    否则客户端等不到回复信号会一直卡住。"""

    async def handle_client(reader, writer):
        conn = SessionConn("err-conn", reader, writer)

        async def on_inbound(frame):
            raise RuntimeError("boom")

        try:
            await conn.run(on_inbound)
        finally:
            writer.close()  # 服务端侧连接须显式关闭（见上例说明）

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write((json.dumps({"type": "input.create", "content": "hi"}) + "\n").encode())
        await writer.drain()

        line = await asyncio.wait_for(reader.readline(), timeout=2)
        msg = json.loads(line)
        assert msg["type"] == "notify"
        assert msg["data"]["type"] == "error"
        assert "boom" in msg["data"]["message"]
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()


async def test_conn_run_processes_consecutive_inputs():
    """input 帧串行处理：按到达顺序交给 on_inbound，不并发乱序。"""
    seen: list[str] = []
    done = asyncio.Event()

    async def handle_client(reader, writer):
        conn = SessionConn("test-conn2", reader, writer)

        async def on_inbound(frame):
            seen.append(frame.get("content") or "")
            if len(seen) == 2:
                done.set()

        try:
            await conn.run(on_inbound)
        finally:
            writer.close()  # 服务端侧连接须显式关闭（见上例说明）

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        for content in ("first", "second"):
            writer.write(
                (json.dumps({"type": "input.create", "content": content}) + "\n").encode()
            )
            await writer.drain()
        await asyncio.wait_for(done.wait(), timeout=2)
        assert seen == ["first", "second"]
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()


async def test_conn_run_switches_agent_without_disconnect():
    """session.* 帧：同一条连接内切换 agent/课程上下文，后续 input 仍正常处理。"""
    switched: list[str] = []
    courses: list[tuple[str, str]] = []
    inputs: list[str] = []
    switched_done = asyncio.Event()
    course_done = asyncio.Event()
    input_done = asyncio.Event()

    async def handle_client(reader, writer):
        conn = SessionConn("switch-conn", reader, writer)

        async def on_inbound(frame):
            kind = frame.get("type")
            if kind == "input.create":
                inputs.append(frame.get("content") or "")
                input_done.set()
            elif kind == "session.set_agent":
                switched.append(str(frame.get("agent") or ""))
                switched_done.set()
            elif kind == "session.set_metadata":
                courses.append(frame.get("values") or {})
                course_done.set()

        try:
            await conn.run(on_inbound)
        finally:
            writer.close()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(
            (
                json.dumps(
                    {"type": "session.set_agent", "agent": "learning"}
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        await asyncio.wait_for(switched_done.wait(), timeout=2)
        assert switched == ["learning"]

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
        await asyncio.wait_for(course_done.wait(), timeout=2)
        assert courses == [{"course_id": "course-a", "lesson_id": "lesson-a"}]

        writer.write((json.dumps({"type": "input.create", "content": "hi"}) + "\n").encode())
        await writer.drain()
        await asyncio.wait_for(input_done.wait(), timeout=2)
        assert inputs == ["hi"]
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()


class DictReplyChannel:
    """模拟真实通道：call 返回 JSON 反序列化后的 dict。"""

    def __init__(self, data: dict):
        self._data = data

    async def notify(self, notification) -> None:
        pass

    async def call(self, service) -> dict:
        return self._data


@pytest.mark.parametrize(
    "reply,expected",
    [
        ({"approved": True}, ApprovalResponse(approved=True)),
        (
            {"approved": False, "reason": "dangerous"},
            ApprovalResponse(approved=False, reason="dangerous"),
        ),
    ],
)
async def test_channel_approver_accepts_dict_reply(reply, expected):
    """ChannelApprover.ask 将真实通道的 dict 回复验证为 ApprovalResponse。"""
    approver = ChannelApprover(DictReplyChannel(reply))
    rsp = await approver.ask("bash", {"cmd": "ls"}, "tc1")
    assert isinstance(rsp, ApprovalResponse)
    assert rsp.approved == expected.approved
    assert rsp.reason == expected.reason


async def test_channel_approver_passthrough_model_reply():
    """内存 / 测试通道直接返回 ApprovalResponse 实例时原样透传。"""
    model = ApprovalResponse(approved=True)

    class ModelChannel(DictReplyChannel):
        async def call(self, service) -> ApprovalResponse:
            return model

    approver = ChannelApprover(ModelChannel(model))
    rsp = await approver.ask("bash", {}, "tc1")
    assert rsp is model


# ── 轮3：attach/resume + 断开 detach 保留 + list 帧 ─────────────────────────


class ServerHarness:
    """测试用 SessionServer 服务端：起真实端口，自带 manager（注入构造参数）。"""

    def __init__(self, db_path: str, storage: str = "memory", path: str | None = None):
        self.manager = SessionManager(
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
            ),
            storage=storage,
            path=path or db_path,
        )
        self.server = SessionServer(sessions=self.manager)

    async def start(self):
        # 用端口 0 让内核分配空闲端口
        import socket

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self._port = port
        self.server._host = "127.0.0.1"
        self.server._port = port
        await self.server.start()
        return port

    async def stop(self):
        await self.server.stop()


async def _connect(port: int, hello: dict):
    """连接测试服务端并发送握手帧，返回 (reader, writer)。"""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write((json.dumps(hello) + "\n").encode())
    await writer.drain()
    return reader, writer


async def _read_line(reader):
    line = await reader.readline()
    if not line:
        return None
    return json.loads(line)


async def test_attach_reattaches_existing_session(tmp_path):
    """attach 帧：同 session_id 重连恢复到 manager 中的会话（resume）。"""
    server = ServerHarness(str(tmp_path / "s.db"))
    manager = server.manager
    port = await server.start()
    try:
        # 首次连接：新建会话
        r1, w1 = await _connect(port, {"type": "hello", "session_id": "session-1"})
        await _read_line(r1)  # welcome
        assert manager.get("session-1") is not None
        await manager.get("session-1")._history.add([UserMessage(content="before")])
        w1.close()
        await w1.wait_closed()
        # 等服务端读到 EOF 并 detach（异步传播）
        for _ in range(100):
            if not manager.get("session-1").attached:
                break
            await asyncio.sleep(0.02)
        assert manager.get("session-1").attached is False

        # 重连：attach 同一会话，attached 恢复
        r2, w2 = await _connect(
            port, {"type": "hello", "session_id": "session-1", "attach": True}
        )
        welcome = await _read_line(r2)
        assert welcome["history"] == [{"role": "user", "content": "before"}]
        assert manager.get("session-1").attached is True
        # 仍是同一个 Session 实例（messages 保留）
        w2.close()
        await w2.wait_closed()
        await asyncio.sleep(0.05)
    finally:
        await server.stop()
        await manager.close()


async def test_history_snapshot_includes_tool_messages(tmp_path):
    """attach 历史包含 assistant tool_calls 与 tool 输出。"""
    server = ServerHarness(str(tmp_path / "s.db"))
    manager = server.manager
    port = await server.start()
    calls = [
        {
            "id": "tc1",
            "type": "function",
            "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
        }
    ]
    r1 = w1 = r2 = w2 = None
    try:
        r1, w1 = await _connect(
            port, {"type": "hello", "session_id": "history-tools"}
        )
        await _read_line(r1)  # welcome
        session = manager.get("history-tools")
        await session._history.add(
            [
                UserMessage(content="run"),
                AssistantMessage(content=None, tool_calls=calls),
                ToolMessage(content="out", tool_call_id="tc1"),
            ]
        )
        w1.close()
        await w1.wait_closed()
        for _ in range(100):
            if not session.attached:
                break
            await asyncio.sleep(0.02)

        r2, w2 = await _connect(
            port,
            {"type": "hello", "session_id": "history-tools", "attach": True},
        )
        welcome = await _read_line(r2)
        assert welcome["history"] == [
            {"role": "user", "content": "run"},
            {"role": "assistant", "content": None, "tool_calls": calls},
            {"role": "tool", "content": "out", "tool_call_id": "tc1"},
        ]
    finally:
        for writer in (w1, w2):
            if writer is not None:
                writer.close()
                await writer.wait_closed()
        await server.stop()
        await manager.close()


async def test_disconnect_detaches_not_closes(tmp_path):
    """连接断开 → 会话 detach 保留（不销毁），manager 中仍存在。"""
    server = ServerHarness(str(tmp_path / "s.db"))
    manager = server.manager
    port = await server.start()
    try:
        r, w = await _connect(port, {"type": "hello", "session_id": "session-2"})
        await _read_line(r)  # welcome
        session = manager.get("session-2")
        assert session is not None
        # 断开前先写一条历史
        await session._history.add([UserMessage(content="before-disconnect")])
        w.close()
        await w.wait_closed()
        # 等服务端读到 EOF 并 detach
        for _ in range(100):
            if not manager.get("session-2").attached:
                break
            await asyncio.sleep(0.02)

        # 会话仍注册、已 detach，且历史保留
        session_after = manager.get("session-2")
        assert session_after is not None
        assert session_after.attached is False
        msgs = await session_after._history.get()
        assert len(msgs) == 1 and msgs[0].content == "before-disconnect"
    finally:
        await server.stop()
        await manager.close()


async def test_disconnect_keeps_active_run(tmp_path):
    """连接断开不取消 run；会话保留并继续执行。"""
    server = ServerHarness(str(tmp_path / "s.db"))
    manager = server.manager
    port = await server.start()
    try:
        r, w = await _connect(port, {"type": "hello", "session_id": "session-3"})
        await _read_line(r)  # welcome
        session = manager.get("session-3")
        started = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def slow_run(*_args, **_kwargs):
            started.set()
            await release.wait()
            finished.set()

        session._agent_runner.run = slow_run  # type: ignore[method-assign]
        w.write((json.dumps({"type": "input.create", "content": "hi"}) + "\n").encode())
        await w.drain()
        await asyncio.wait_for(started.wait(), 1)

        w.close()
        await w.wait_closed()
        for _ in range(100):
            if not session.attached:
                break
            await asyncio.sleep(0.02)
        assert session.attached is False
        assert session.state == "running"

        release.set()
        await asyncio.wait_for(finished.wait(), 2)
        for _ in range(100):
            if session.state == "idle":
                break
            await asyncio.sleep(0.02)
        assert session.state == "idle"
        assert manager.get("session-3") is session
    finally:
        await server.stop()
        await manager.close()


async def test_reconnect_replays_current_turn_events(tmp_path):
    """断连期间产生的当前 turn 事件，重连后补发。"""
    server = ServerHarness(str(tmp_path / "s.db"))
    manager = server.manager
    port = await server.start()
    r1 = w1 = r2 = w2 = None
    try:
        r1, w1 = await _connect(port, {"type": "hello", "session_id": "replay"})
        await _read_line(r1)  # welcome
        session = manager.get("replay")
        started = asyncio.Event()
        release = asyncio.Event()
        emitted = asyncio.Event()
        finish = asyncio.Event()

        async def stream_run(*_args, **_kwargs):
            started.set()
            await release.wait()
            await session._bus.emit(MessageDeltaEvent(delta="hello"))
            emitted.set()
            await finish.wait()

        session._agent_runner.run = stream_run  # type: ignore[method-assign]
        w1.write((json.dumps({"type": "input.create", "content": "hi"}) + "\n").encode())
        await w1.drain()
        await asyncio.wait_for(started.wait(), 1)

        w1.close()
        await w1.wait_closed()
        for _ in range(100):
            if not session.attached:
                break
            await asyncio.sleep(0.02)
        release.set()
        await asyncio.wait_for(emitted.wait(), 1)

        r2, w2 = await _connect(
            port, {"type": "hello", "session_id": "replay", "attach": True}
        )
        assert (await _read_line(r2))["type"] == "welcome"
        replay = await asyncio.wait_for(_read_line(r2), 2)
        assert replay["type"] == "notify"
        assert replay["data"]["type"] == "delta"
        assert replay["data"]["delta"] == "hello"

        finish.set()
        for _ in range(100):
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


async def test_attach_unknown_session_errors(tmp_path):
    """attach 不存在的会话 → 服务端回 error 帧。"""
    server = ServerHarness(str(tmp_path / "s.db"))
    manager = server.manager
    port = await server.start()
    try:
        r, w = await _connect(
            port, {"type": "hello", "session_id": "ghost", "attach": True}
        )
        msg = await _read_line(r)
        assert msg["type"] == "error"
        assert "不存在" in msg["message"]
        w.close()
        await w.wait_closed()
    finally:
        await server.stop()
        await manager.close()


async def test_list_frame_returns_sessions(tmp_path):
    """list 帧：返回 manager 中全部会话（含 attached 状态）。"""
    server = ServerHarness(str(tmp_path / "s.db"))
    manager = server.manager
    port = await server.start()
    try:
        # 建两个会话，一个断开
        r1, w1 = await _connect(port, {"type": "hello", "session_id": "a"})
        await _read_line(r1)
        r2, w2 = await _connect(port, {"type": "hello", "session_id": "b"})
        await _read_line(r2)
        w1.close()
        await w1.wait_closed()
        # 等服务端 detach a
        for _ in range(100):
            if not manager.get("a").attached:
                break
            await asyncio.sleep(0.02)

        # list 帧
        r3, w3 = await _connect(port, {"type": "list"})
        msg = await _read_line(r3)
        sessions = {s["session_id"]: s for s in msg["sessions"]}
        assert set(sessions) == {"a", "b"}
        assert sessions["a"]["attached"] is False  # 已断开
        assert sessions["b"]["attached"] is True
        w3.close()
        await w3.wait_closed()
    finally:
        await server.stop()
        await manager.close()


async def test_lists_workspaces_and_history_sessions(tmp_path):
    """浏览帧：工作目录和目录下的历史会话来自文件存储。"""
    sessions = tmp_path / "sessions"
    project = tmp_path / "project"
    project.mkdir()
    server = ServerHarness(
        str(tmp_path / "s.db"), storage="file", path=str(sessions)
    )
    manager = server.manager
    port = await server.start()
    try:
        r1, w1 = await _connect(port, {"type": "list_workspaces"})
        empty = await _read_line(r1)
        assert empty["type"] == "workspaces"
        assert empty["current"]
        assert empty["workspaces"] == []
        w1.close()
        await w1.wait_closed()

        r2, w2 = await _connect(
            port,
            {"type": "hello", "session_id": "file-session", "cwd": str(project)},
        )
        welcome = await _read_line(r2)
        assert welcome["type"] == "welcome"
        assert welcome["history"] == []
        session = manager.get("file-session")
        assert session is not None
        await session._history.add([UserMessage(content="hello workspace")])

        r3, w3 = await _connect(
            port, {"type": "list_sessions", "cwd": str(project)}
        )
        listed = await _read_line(r3)
        assert listed["type"] == "sessions"
        assert [item["session_id"] for item in listed["sessions"]] == [
            "file-session"
        ]
        assert listed["sessions"][0]["title"] == "hello workspace"
        w3.close()
        await w3.wait_closed()

        r4, w4 = await _connect(port, {"type": "list_workspaces"})
        workspaces = await _read_line(r4)
        assert workspaces["type"] == "workspaces"
        assert [item["path"] for item in workspaces["workspaces"]] == [
            str(project.resolve())
        ]
        w4.close()
        await w4.wait_closed()

        r5, w5 = await _connect(
            port,
            {"type": "list_sessions", "cwd": str(project / "missing")},
        )
        invalid = await _read_line(r5)
        assert invalid["type"] == "error"
        assert invalid["code"] == "invalid_cwd"
        w5.close()
        await w5.wait_closed()
        w2.close()
        await w2.wait_closed()
    finally:
        await server.stop()
        await manager.close()


async def test_hello_call_timeout_override(tmp_path):
    """hello 显式 call_timeout=null → 不设交互超时；缺省仍为 CALL_TIMEOUT。"""
    server = ServerHarness(str(tmp_path / "s.db"))
    manager = server.manager
    port = await server.start()
    try:
        r1, w1 = await _connect(
            port, {"type": "hello", "session_id": "no-timeout", "call_timeout": None}
        )
        await _read_line(r1)  # welcome
        assert manager.get("no-timeout").transport._call_timeout is None
        w1.close()
        await w1.wait_closed()

        r2, w2 = await _connect(port, {"type": "hello", "session_id": "default"})
        await _read_line(r2)
        assert manager.get("default").transport._call_timeout == CALL_TIMEOUT
        w2.close()
        await w2.wait_closed()
    finally:
        await server.stop()
        await manager.close()


async def test_unknown_frame_reports_error(tmp_path):
    """未知 session.* 帧由上层拒绝，连接保持可用。"""
    server = ServerHarness(str(tmp_path / "s.db"))
    port = await server.start()
    try:
        reader, writer = await _connect(
            port, {"type": "hello", "session_id": "unknown-frame"}
        )
        await _read_line(reader)  # welcome
        writer.write(
            (json.dumps({"type": "session.unknown"}) + "\n").encode()
        )
        await writer.drain()
        msg = await asyncio.wait_for(_read_line(reader), 2)
        assert msg["type"] == "notify"
        assert msg["data"]["type"] == "error"
        assert msg["data"]["code"] == "control_failed"
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()
        await server.manager.close()


async def test_run_cancel_is_routed_to_inbound():
    """run.cancel 和其他控制帧统一走 on_inbound。"""
    seen: list[str] = []
    done = asyncio.Event()

    async def handle_client(reader, writer):
        conn = SessionConn("cancel-conn", reader, writer)

        async def on_inbound(frame):
            seen.append(str(frame.get("type")))
            done.set()

        try:
            await conn.run(on_inbound)
        finally:
            writer.close()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write((json.dumps({"type": "run.cancel"}) + "\n").encode())
        await writer.drain()
        await asyncio.wait_for(done.wait(), 2)
        assert seen == ["run.cancel"]
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()
