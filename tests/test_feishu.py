"""FeishuChannel（官方 lark-channel-sdk）：流式卡片、工具精简、卡片按钮审批。"""
import asyncio
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


class FakeStream:
    """模拟官方 CardKit stream 对象：记录 append 的 chunks。"""

    def __init__(self):
        self.chunks: list[str] = []

    async def append(self, chunk: str) -> None:
        self.chunks.append(chunk)


def _make_channel(streams: list[FakeStream] | None = None) -> FeishuChannel:
    """构建 mock 掉官方 SDK 的通道。

    stream() 必须模拟真实阻塞语义：producer 消费队列直到读到 None 才返回。
    用 AsyncMock（调用即返回）会让 _run_stream 的 while True 变忙循环，
    CPU 100% 拖垮 WSL。
    """
    c = FeishuChannel(app_id="cli_test", app_secret="secret")
    c._channel = AsyncMock()
    c._channel.send.return_value = MagicMock(success=True, message_id="msg_1")

    async def fake_stream(chat_id, payload):
        stream = FakeStream()
        if streams is not None:
            streams.append(stream)
        await payload["markdown"](stream)  # 阻塞：消费队列直到收到 None
        return stream

    c._channel.stream.side_effect = fake_stream
    c._channel.on = MagicMock(return_value=lambda: None)  # 同步注册，返回 Unsubscribe
    return c


@pytest.fixture
async def ch():
    streams: list[FakeStream] = []
    c = _make_channel(streams)
    await c.start()  # 注册回调并连接（流任务懒启动，首个 Delta 时创建）
    c._chat_id = "oc_test"
    c._test_streams = streams  # 测试断言 append 的 chunks
    yield c
    await c.stop()


# ── notify：流式卡片 ─────────────────────────────────
async def test_stream_delta_accumulates(ch):
    """Delta 流式入队，常驻任务 producer 累积 append 到卡片流。"""
    await ch.notify(Delta(delta="你"))
    await ch.notify(Delta(delta="好"))
    await asyncio.sleep(0.05)
    assert ch._test_streams
    assert ch._test_streams[-1].chunks == ["你", "好"]


async def test_delta_end_sends_end_signal(ch):
    """DeltaEnd（None）结束当前卡片流，常驻任务循环新开下一回合。"""
    await ch.notify(Delta(delta="a"))
    await ch.notify(DeltaEnd())
    await asyncio.sleep(0.05)
    # 第一回合已 finish（a 被消费），任务已新开第二回合（等待新 Delta）
    assert len(ch._test_streams) == 2
    assert ch._test_streams[0].chunks == ["a"]
    assert ch._test_streams[1].chunks == []  # 第二回合等待新 Delta
    # 队列常驻，下回合直接复用
    await ch.notify(Delta(delta="b"))
    await asyncio.sleep(0.05)
    assert ch._test_streams[1].chunks == ["b"]


# ── notify：工具事件 ────────────────────────────────
async def test_tool_start_card(ch):
    """ToolStart 以卡片展示：🔧 工具名 + 参数。"""
    await ch.notify(ToolStart(tool_name="bash", arguments={"cmd": "ls"}, tool_call_id="1"))
    ch._channel.send.assert_awaited_once()
    args = ch._channel.send.await_args.args
    assert args[0] == "oc_test"
    card = args[1]["card"]
    assert card["header"]["title"]["content"] == "🔧 bash"
    body = " ".join(e.get("content", "") for e in card["body"]["elements"])
    assert "**bash**" in body and '"cmd"' in body and '"ls"' in body


@pytest.mark.parametrize(
    ("n", "marker"),
    [
        (ToolResult(tool_call_id="1", error="boom"), "❌ 工具错误"),
        (Error(message="oops"), "⚠️"),
    ],
)
async def test_error_rendered(ch, n, marker):
    """错误类通知按 markdown 发送（chat_id + 内容标记）。"""
    await ch.notify(n)
    ch._channel.send.assert_awaited_once()
    args = ch._channel.send.await_args.args
    assert args[0] == "oc_test"
    assert marker in args[1]["markdown"]


async def test_tool_result_success_silent(ch):
    """ToolResult 成功详情静默（精简，不发）。"""
    await ch.notify(ToolResult(tool_call_id="1", result={"status": "ok"}))
    ch._channel.send.assert_not_awaited()


async def test_notify_failure_no_raise():
    """推送失败仅告警，不抛异常（不拖垮主链路）。"""
    c = _make_channel()
    c._chat_id = "oc_test"
    c._channel.send.side_effect = Exception("network down")
    await c.notify(Delta(delta="x"))  # 不应抛出
    await c.notify(ToolStart(tool_name="bash", arguments={}, tool_call_id="1"))  # 不应抛出
    await c.stop()


# ── call：接收 / 审批 ────────────────────────────────
async def test_call_receive_from_queue(ch):
    """call(Receive) 从事件队列取用户消息。"""
    await ch._queue.put(UserInput(content="你好"))
    rsp = await ch.call(Receive())
    assert rsp.content == "你好"


async def test_on_message_enqueues():
    """收到官方 InboundMessage → 入队 UserInput（跨线程桥接）。"""
    c = _make_channel()
    await c.start()  # 注册闭包回调（捕获主 loop）
    c._on_message(FakeMsg(chat_id="oc_x", text="  hello  "))
    assert (await c._queue.get()).content == "hello"
    assert c._chat_id == "oc_x"
    await c.stop()


async def _click(c: FeishuChannel, decision: str) -> None:
    """等待审批卡片注册工具调用 id，模拟点击按钮（带超时保护）。"""
    for _ in range(1000):
        if c._pending_approvals:
            break
        await asyncio.sleep(0)  # 让 call(Approval) task 推进到注册 Future
    assert c._pending_approvals
    tc_id = next(iter(c._pending_approvals))
    c._on_card_action(FakeCardAction({"tool_call_id": tc_id, "decision": decision}))


@pytest.mark.parametrize(
    ("decision", "expected", "feedback"),
    [
        ("approve", ApprovalRsp(approved=True, reason=None), "✅ 已通过"),
        ("reject", ApprovalRsp(approved=False, reason="用户拒绝"), "❌ 已拒绝"),
    ],
)
async def test_approval(decision, expected, feedback):
    """审批：按钮回调 → ApprovalRsp，反馈卡片保留工具调用信息。"""
    c = _make_channel()
    await c.start()  # 注册闭包回调
    c._chat_id = "oc_test"
    task = asyncio.create_task(
        c.call(Approval(tool_name="t", arguments={"cmd": "x"}, tool_call_id="tc1"))
    )
    await _click(c, decision)
    rsp = await task
    assert rsp == expected
    # 审批卡含按钮与工具信息
    card = c._channel.send.await_args.args[1]["card"]
    assert card["header"]["title"]["content"] == "🔐 工具审批"
    # 反馈卡：状态 + 保留工具调用信息
    c._channel.update_card.assert_awaited_once()
    args = c._channel.update_card.await_args.args
    assert args[0] == "msg_1"
    card = args[1]
    assert feedback in card["header"]["title"]["content"]
    body = " ".join(e.get("content", "") for e in card["body"]["elements"])
    assert "**t**" in body and '"cmd"' in body
    await c.stop()
