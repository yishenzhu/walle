"""CLI 通道回归测试：读循环并发与审批回复验证。

覆盖修复：
- CLIConn.run 读循环不被 on_input 阻塞（agent 运行中仍能读取 reply 帧，
  否则 ask_user / 审批等双向交互会死锁）。
- ChannelApprover.ask 接受真实通道返回的 dict（JSON 反序列化），验证为模型。
"""
import asyncio
import json

import pytest

from ..channel.cli import CLIConn
from ..core.approval import ChannelApprover
from ..schemas import Approval, ApprovalRsp


async def test_conn_run_processes_reply_while_input_in_flight():
    """读循环不被 on_input 占住：agent 运行中等待的审批 reply 必须被读到。"""
    results: dict = {}

    async def handle_client(reader, writer):
        conn = CLIConn("test-conn", reader, writer)

        async def on_input(content):
            # 模拟 agent run：处理消息期间发起审批 call 并等待 reply
            rsp = await conn.call(
                Approval(tool_name="bash", arguments={}, tool_call_id="tc1")
            )
            results["approval"] = rsp

        await conn.run(on_input)

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write((json.dumps({"type": "input", "content": "hi"}) + "\n").encode())
        await writer.drain()

        # 服务端 agent（on_input）应发出审批 call 帧
        line = await asyncio.wait_for(reader.readline(), timeout=2)
        msg = json.loads(line)
        assert msg["type"] == "call"
        assert msg["data"]["type"] == "approval"

        # 回 reply：修复前读循环被 on_input 占住，reply 无人读取 → on_input 永不完成
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


async def test_conn_run_processes_consecutive_inputs():
    """input 帧串行处理：按到达顺序执行 on_input，不并发乱序。"""
    seen: list[str] = []
    done = asyncio.Event()

    async def handle_client(reader, writer):
        conn = CLIConn("test-conn2", reader, writer)

        async def on_input(content):
            seen.append(content)
            if len(seen) == 2:
                done.set()

        await conn.run(on_input)

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        for content in ("first", "second"):
            writer.write(
                (json.dumps({"type": "input", "content": content}) + "\n").encode()
            )
            await writer.drain()
        await asyncio.wait_for(done.wait(), timeout=2)
        assert seen == ["first", "second"]
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()


class _DictReplyChannel:
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
        ({"approved": True}, ApprovalRsp(approved=True)),
        ({"approved": False, "reason": "dangerous"}, ApprovalRsp(approved=False, reason="dangerous")),
    ],
)
async def test_channel_approver_accepts_dict_reply(reply, expected):
    """ChannelApprover.ask 将真实通道的 dict 回复验证为 ApprovalRsp。"""
    approver = ChannelApprover(_DictReplyChannel(reply))
    rsp = await approver.ask("bash", {"cmd": "ls"}, "tc1")
    assert isinstance(rsp, ApprovalRsp)
    assert rsp.approved == expected.approved
    assert rsp.reason == expected.reason


async def test_channel_approver_passthrough_model_reply():
    """内存 / 测试通道直接返回 ApprovalRsp 实例时原样透传。"""
    model = ApprovalRsp(approved=True)

    class _ModelChannel(_DictReplyChannel):
        async def call(self, service) -> ApprovalRsp:
            return model

    approver = ChannelApprover(_ModelChannel(model))
    rsp = await approver.ask("bash", {}, "tc1")
    assert rsp is model


# ── 轮3：attach/resume + 断开 detach 保留 + list 帧 ─────────────────────────

from ..core import SessionRegistry, Session, Runner, Agent, ToolExecutor
from ..conf import ToolConfig, ApprovalConfig, ApprovalDecision
from ..channel.cli import CLIChannel
from ..schemas import UserMessage


class _TestServer:
    """测试用 CLIChannel 服务端：起真实端口，自带 registry（注入构造参数）。"""

    def __init__(self, db_path: str):
        self.registry = SessionRegistry(
            agent_factory=lambda _name: Agent(instruction="You are a helpful assistant."),
            runner=Runner(executor=ToolExecutor(ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
            ))),
            db_path=db_path,
        )
        self.channel = CLIChannel(registry=self.registry)

    async def start(self):
        # 用端口 0 让内核分配空闲端口
        import socket

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self._port = port
        self.channel._host = "127.0.0.1"
        self.channel._port = port
        await self.channel.start()
        return port

    async def stop(self):
        await self.channel.stop()


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


async def test_cli_attach_reattaches_existing_session(tmp_path):
    """attach 帧：同 chat_id 重连恢复到 registry 中的会话（resume）。"""
    server = _TestServer(str(tmp_path / "s.db"))
    reg = server.registry
    port = await server.start()
    try:
        # 首次连接：新建会话
        r1, w1 = await _connect(port, {"type": "hello", "chat_id": "sess-1"})
        await _read_line(r1)  # welcome
        assert reg.get("sess-1") is not None
        w1.close()
        await w1.wait_closed()
        # 等服务端读到 EOF 并 detach（异步传播）
        for _ in range(100):
            if not reg.get("sess-1").attached:
                break
            await asyncio.sleep(0.02)
        assert reg.get("sess-1").attached is False

        # 重连：attach 同一会话，attached 恢复
        r2, w2 = await _connect(port, {"type": "hello", "chat_id": "sess-1", "attach": True})
        await _read_line(r2)  # welcome
        assert reg.get("sess-1").attached is True
        # 仍是同一个 Session 实例（kernel/messages 保留）
        w2.close()
        await w2.wait_closed()
        await asyncio.sleep(0.05)
    finally:
        await server.stop()
        await reg.close()


async def test_cli_disconnect_detaches_not_closes(tmp_path):
    """连接断开 → 会话 detach 保留（不销毁），registry 中仍存在。"""
    server = _TestServer(str(tmp_path / "s.db"))
    reg = server.registry
    port = await server.start()
    try:
        r, w = await _connect(port, {"type": "hello", "chat_id": "sess-2"})
        await _read_line(r)  # welcome
        sess = reg.get("sess-2")
        assert sess is not None
        # 断开前先写一条历史
        await sess._messages.add([UserMessage(content="before-disconnect")])
        w.close()
        await w.wait_closed()
        # 等服务端读到 EOF 并 detach
        for _ in range(100):
            if not reg.get("sess-2").attached:
                break
            await asyncio.sleep(0.02)

        # 会话仍注册、已 detach，且历史保留
        sess2 = reg.get("sess-2")
        assert sess2 is not None
        assert sess2.attached is False
        msgs = await sess2._messages.get()
        assert len(msgs) == 1 and msgs[0].content == "before-disconnect"
    finally:
        await server.stop()
        await reg.close()


async def test_cli_attach_unknown_session_errors(tmp_path):
    """attach 不存在的会话 → 服务端回 error 帧。"""
    server = _TestServer(str(tmp_path / "s.db"))
    reg = server.registry
    port = await server.start()
    try:
        r, w = await _connect(port, {"type": "hello", "chat_id": "ghost", "attach": True})
        msg = await _read_line(r)
        assert msg["type"] == "error"
        assert "不存在" in msg["message"]
        w.close()
        await w.wait_closed()
    finally:
        await server.stop()
        await reg.close()


async def test_cli_list_frame_returns_sessions(tmp_path):
    """list 帧：返回 registry 中全部会话（含 attached 状态）。"""
    server = _TestServer(str(tmp_path / "s.db"))
    reg = server.registry
    port = await server.start()
    try:
        # 建两个会话，一个断开
        r1, w1 = await _connect(port, {"type": "hello", "chat_id": "a"})
        await _read_line(r1)
        r2, w2 = await _connect(port, {"type": "hello", "chat_id": "b"})
        await _read_line(r2)
        w1.close()
        await w1.wait_closed()
        # 等服务端 detach a
        for _ in range(100):
            if not reg.get("a").attached:
                break
            await asyncio.sleep(0.02)

        # list 帧
        r3, w3 = await _connect(port, {"type": "list"})
        msg = await _read_line(r3)
        sessions = {s["session_id"]: s for s in msg["sessions"]}
        assert set(sessions) == {"a", "b"}
        assert sessions["a"]["attached"] is False   # 已断开
        assert sessions["b"]["attached"] is True
        w3.close()
        await w3.wait_closed()
    finally:
        await server.stop()
        await reg.close()
