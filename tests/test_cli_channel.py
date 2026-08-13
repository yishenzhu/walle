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
