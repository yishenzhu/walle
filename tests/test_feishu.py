"""FeishuChannel：流式打字机（创建→更新）、工具事件独立发、交互。"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from walle.channel import FeishuChannel
from walle.schemas import Delta, DeltaEnd, Error, ToolStart, Receive, UserInput


class FakeResp:
    def __init__(self, code=0, message_id="om_1"):
        self.code = code
        self.msg = "success"
        self.data = type("D", (), {"message_id": message_id})()


@pytest.fixture
def ch():
    c = FeishuChannel(app_id="cli_test", app_secret="secret")
    c._chat_id = "oc_test"
    # mock SDK client 的 async 方法
    c._client = AsyncMock()
    c._client.im.v1.message.acreate.return_value = FakeResp(message_id="om_1")
    c._client.im.v1.message.aupdate.return_value = FakeResp()
    return c


async def test_stream_creates_then_updates(ch):
    """首条 Delta 创建消息，后续 Delta 节流更新（打字机）。"""
    times = iter([0.0, 1.5])  # 第二次 delta 距上次超过 1s，触发更新
    with patch("walle.channel.feishu.time.monotonic", side_effect=lambda: next(times)):
        await ch.notify(Delta(delta="你"))
        await ch.notify(Delta(delta="好"))
    assert ch._client.im.v1.message.acreate.await_count == 1
    assert ch._client.im.v1.message.aupdate.await_count == 1
    # 更新内容为累积文本
    update_req = ch._client.im.v1.message.aupdate.await_args.args[0]
    content = json.loads(update_req.request_body.content)["text"]
    assert content == "你好"
    # 更新的是创建返回的 message_id
    assert update_req.message_id == "om_1"


async def test_stream_throttles_within_second(ch):
    """同一秒内的 Delta 不重复更新（节流，避免超 20 次编辑限制）。"""
    times = iter([0.0, 0.3])  # 间隔 < 1s，第二次不更新
    with patch("walle.channel.feishu.time.monotonic", side_effect=lambda: next(times)):
        await ch.notify(Delta(delta="a"))
        await ch.notify(Delta(delta="b"))
        await ch.notify(Delta(delta="c"))
    assert ch._client.im.v1.message.acreate.await_count == 1
    assert ch._client.im.v1.message.aupdate.await_count == 0  # 全部在 1s 内，无更新


async def test_delta_end_finalizes(ch):
    """DeltaEnd 后回合定格，新回合重新创建。"""
    await ch.notify(Delta(delta="a"))
    await ch.notify(DeltaEnd())
    ch._client.im.v1.message.acreate.reset_mock()
    await ch.notify(Delta(delta="b"))
    assert ch._client.im.v1.message.acreate.await_count == 1  # 新回合重新创建


async def test_tool_event_sent_independently(ch):
    await ch.notify(ToolStart(tool_name="bash", arguments={"cmd": "ls"}, tool_call_id="1"))
    assert ch._client.im.v1.message.acreate.await_count == 1
    create_req = ch._client.im.v1.message.acreate.await_args.args[0]
    assert "🔧 **bash**" in json.loads(create_req.request_body.content)["text"]


async def test_error_sent(ch):
    await ch.notify(Error(message="oops"))
    assert ch._client.im.v1.message.acreate.await_count == 1


async def test_call_receive_from_queue(ch):
    """call(Receive) 从事件队列取用户消息。"""
    await ch._queue.put(UserInput(content="你好"))
    rsp = await ch.call(Receive())
    assert rsp.content == "你好"


async def test_notify_failure_no_raise(ch):
    """推送失败仅告警，不抛异常（不拖垮主链路）。"""
    c = FeishuChannel(app_id="cli_test", app_secret="secret")
    c._chat_id = "oc_test"
    c._client = AsyncMock()
    c._client.im.v1.message.acreate.side_effect = Exception("network down")
    await c.notify(Delta(delta="x"))  # 不应抛出


def test_render_format():
    """_render 格式：工具名加粗 + 参数 JSON 代码块；结果 dict 格式化。"""
    from walle.schemas import ToolResult

    start = FeishuChannel._render(ToolStart(tool_name="bash", arguments={"cmd": "ls"}, tool_call_id="1"))
    assert "🔧 **bash**" in start
    assert "```" in start

    result = FeishuChannel._render(ToolResult(tool_call_id="1", result={"status": "ok"}))
    assert "✅ 工具结果" in result
    assert '"status": "ok"' in result  # JSON 格式化

    err = FeishuChannel._render(ToolResult(tool_call_id="1", error="boom"))
    assert "❌ 工具错误" in err
    assert "boom" in err
