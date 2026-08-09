"""FeishuChannel：流式打字机（创建→更新）、工具事件独立发、交互。"""
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from walle.channel import FeishuChannel
from walle.schemas import Delta, DeltaEnd, Error, ToolStart, Receive, UserInput


def _resp(json_body: dict) -> httpx.Response:
    return httpx.Response(
        200, json=json_body, request=httpx.Request("POST", "https://open.feishu.cn")
    )


@pytest.fixture
def ch():
    c = FeishuChannel(app_id="cli_test", app_secret="secret")
    c._chat_id = "oc_test"
    c._http.post = AsyncMock()
    c._http.put = AsyncMock()

    async def fake_post(url, **kwargs):
        if "auth/v3/tenant_access_token" in url:
            return _resp({"tenant_access_token": "t-1"})
        return _resp({"data": {"message_id": "om_1"}})

    c._http.post.side_effect = fake_post
    return c


def _creates(ch):
    return [c for c in ch._http.post.call_args_list if "messages" in c.args[0]]


def _text(call):
    """从发消息调用的 content 字段解析出纯文本。"""
    return json.loads(call.kwargs["json"]["content"])["text"]


async def test_stream_creates_then_updates(ch):
    """首条 Delta 创建消息，后续 Delta 更新同一消息（打字机）。"""
    await ch.notify(Delta(delta="你"))
    await ch.notify(Delta(delta="好"))
    assert len(_creates(ch)) == 1
    assert len(ch._http.put.call_args_list) == 1
    put_payload = ch._http.put.await_args.kwargs["json"]
    assert "你好" in json.loads(put_payload["content"])["text"]
    assert ch._http.put.call_args.args[0].endswith("/om_1")


async def test_delta_end_finalizes(ch):
    """DeltaEnd 后回合定格，新回合重新创建。"""
    await ch.notify(Delta(delta="a"))
    await ch.notify(DeltaEnd())
    ch._http.post.call_args_list.clear()
    await ch.notify(Delta(delta="b"))
    assert len(_creates(ch)) == 1  # 新回合重新创建


async def test_tool_event_sent_independently(ch):
    await ch.notify(ToolStart(tool_name="bash", arguments={"cmd": "ls"}, tool_call_id="1"))
    assert len(_creates(ch)) == 1
    assert "🔧 **bash**" in _text(_creates(ch)[0])


async def test_error_sent(ch):
    await ch.notify(Error(message="oops"))
    assert len(_creates(ch)) == 1


async def test_call_receive_from_queue(ch):
    """call(Receive) 从事件队列取用户消息。"""
    await ch._queue.put(UserInput(content="你好"))
    rsp = await ch.call(Receive())
    assert rsp.content == "你好"


async def test_notify_failure_no_raise(ch):
    """推送失败仅告警，不抛异常（不拖垮主链路）。"""
    c = FeishuChannel(app_id="cli_test", app_secret="secret")
    c._chat_id = "oc_test"
    c._http.post = AsyncMock(side_effect=Exception("network down"))
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
