"""FeishuChannel（官方 lark-channel-sdk）：流式卡片、工具精简、卡片按钮审批。"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from walle.channel import FeishuChannel
from walle.schemas import (
    Approval,
    ApprovalRsp,
    Delta,
    DeltaEnd,
    Error,
    Receive,
    ToolResult,
    ToolStart,
    UserInput,
)


class FakeMsg:
    """模拟官方 InboundMessage。"""

    def __init__(self, chat_id="oc_test", text="你好"):
        self.conversation = MagicMock()
        self.conversation.chat_id = chat_id
        self.body_text = text
        self.content_text = text


class FakeCardAction:
    """模拟官方 CardActionEvent。"""

    def __init__(self, value: dict):
        self.action = MagicMock()
        self.action.value = value


@pytest.fixture
def ch():
    c = FeishuChannel(app_id="cli_test", app_secret="secret")
    c._chat_id = "oc_test"
    # mock 官方 SDK channel 的 async 方法
    c._channel = AsyncMock()
    c._channel.send.return_value = MagicMock(success=True)
    c._channel.stream.return_value = MagicMock(success=True)
    return c


# ── notify：流式卡片 ─────────────────────────────────
async def test_stream_delta_feeds_queue(ch):
    """首个 Delta 启动流式队列 + 后台 stream 任务。"""
    await ch.notify(Delta(delta="你"))
    assert ch._stream_queue is not None
    # 队列里有这个 delta
    assert await ch._stream_queue.get() == "你"
    # 后台 _run_stream 已启动并调用官方 stream
    await asyncio.sleep(0.01)
    ch._channel.stream.assert_awaited()


async def test_stream_delta_accumulates(ch):
    """多个 Delta 全部入队，官方 stream 的 producer 消费累积。"""
    await ch.notify(Delta(delta="你"))
    await ch.notify(Delta(delta="好"))
    q = ch._stream_queue
    assert await q.get() == "你"
    assert await q.get() == "好"


async def test_delta_end_sends_end_signal(ch):
    """DeltaEnd 入队 None 结束信号，新回合重新启动流。"""
    await ch.notify(Delta(delta="a"))
    await ch.notify(DeltaEnd())
    assert ch._stream_queue is None  # 已重置
    # 新回合：重新创建流
    await ch.notify(Delta(delta="b"))
    assert ch._stream_queue is not None


# ── notify：工具事件精简 ─────────────────────────────
async def test_tool_start_sent(ch):
    """ToolStart 独立发送（markdown，参数截断）。"""
    await ch.notify(ToolStart(tool_name="bash", arguments={"cmd": "ls"}, tool_call_id="1"))
    ch._channel.send.assert_awaited_once()
    args = ch._channel.send.await_args.args
    assert args[0] == "oc_test"
    assert "🔧 **bash**" in args[1]["markdown"]


async def test_tool_result_success_silent(ch):
    """ToolResult 成功详情静默（精简，不发）。"""
    await ch.notify(ToolResult(tool_call_id="1", result={"status": "ok"}))
    ch._channel.send.assert_not_awaited()


async def test_tool_result_error_sent(ch):
    """ToolResult 错误发送（❌）。"""
    await ch.notify(ToolResult(tool_call_id="1", error="boom"))
    ch._channel.send.assert_awaited_once()
    assert "❌ 工具错误" in ch._channel.send.await_args.args[1]["markdown"]


async def test_error_sent(ch):
    await ch.notify(Error(message="oops"))
    ch._channel.send.assert_awaited_once()
    assert "⚠️" in ch._channel.send.await_args.args[1]["markdown"]


async def test_notify_failure_no_raise():
    """推送失败仅告警，不抛异常（不拖垮主链路）。"""
    c = FeishuChannel(app_id="cli_test", app_secret="secret")
    c._chat_id = "oc_test"
    c._channel = AsyncMock()
    c._channel.send.side_effect = Exception("network down")
    await c.notify(Delta(delta="x"))  # 不应抛出
    await c.notify(ToolStart(tool_name="bash", arguments={}, tool_call_id="1"))  # 不应抛出


# ── call：接收 / 审批 ────────────────────────────────
async def test_call_receive_from_queue(ch):
    """call(Receive) 从事件队列取用户消息。"""
    await ch._queue.put(UserInput(content="你好"))
    rsp = await ch.call(Receive())
    assert rsp.content == "你好"


async def test_on_message_enqueues(ch):
    """收到官方 InboundMessage → 入队 UserInput（跨线程桥接）。"""
    c = FeishuChannel(app_id="cli_test", app_secret="secret")
    c._queue = asyncio.Queue()
    c._loop = MagicMock()
    c._loop.call_soon_threadsafe = lambda fn, *a, **k: fn(*a, **k)
    c._on_message(FakeMsg(chat_id="oc_x", text="  hello  "))
    assert (await c._queue.get()).content == "hello"
    assert c._chat_id == "oc_x"


async def _approval_channel(ch) -> FeishuChannel:
    """构建审批测试通道：真实 loop + mock SDK。"""
    c = FeishuChannel(app_id="cli_test", app_secret="secret")
    c._chat_id = "oc_test"
    c._channel = ch._channel
    c._loop = asyncio.get_running_loop()
    return c


async def _click(c: FeishuChannel, decision: str) -> None:
    """等待审批卡片发出，模拟点击按钮。"""
    await asyncio.sleep(0.01)
    token = next(iter(c._pending_approvals))
    c._on_card_action(FakeCardAction({"approval": token, "decision": decision}))


async def test_approval_card_button(ch):
    """审批：卡片按钮回调 → ApprovalRsp。"""
    c = await _approval_channel(ch)
    task = asyncio.create_task(
        c.call(Approval(tool_name="mcp_tavily", arguments={"query": "x"}))
    )
    await _click(c, "approve")
    rsp = await task
    assert rsp == ApprovalRsp(approved=True, reason=None)
    # 卡片含审批按钮（CardPayload.data 是卡片 dict）
    card = c._channel.send.await_args.args[1]["card"]
    card = getattr(card, "data", card)
    assert "🔐 工具审批" in card["header"]["title"]["content"]


async def test_approval_reject(ch):
    """审批：❌ 拒绝 → ApprovalRsp(approved=False)。"""
    c = await _approval_channel(ch)
    task = asyncio.create_task(c.call(Approval(tool_name="t", arguments={})))
    await _click(c, "reject")
    rsp = await task
    assert rsp.approved is False
    assert rsp.reason == "用户拒绝"
