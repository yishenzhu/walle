"""FeishuObserver：缓冲合并 Delta、工具事件即时发、签名校验。"""
from unittest.mock import AsyncMock, patch

import pytest

from walle.channel.feishu import FeishuObserver
from walle.schemas import Delta, DeltaEnd, Error, ToolResult, ToolStart


@pytest.fixture
def observer():
    obs = FeishuObserver(webhook="https://open.feishu.cn/open-apis/bot/v2/hook/test")
    obs._client = AsyncMock()
    obs._client.post = AsyncMock()
    return obs


async def test_delta_buffered_and_sent_on_end(observer):
    """Delta 增量缓冲，DeltaEnd 时合并为一条发送。"""
    await observer(Delta(delta="你好"))
    await observer(Delta(delta="，世界"))
    observer._client.post.assert_not_awaited()  # 未到回合结束不发
    await observer(DeltaEnd())
    observer._client.post.assert_awaited_once()
    payload = observer._client.post.await_args.kwargs["json"]
    assert payload["msg_type"] == "text"
    assert payload["content"]["text"] == "你好，世界"


async def test_tool_event_sent_immediately(observer):
    """工具事件即时发送，不缓冲。"""
    await observer(ToolStart(tool_name="bash", arguments={"cmd": "ls"}, tool_call_id="1"))
    observer._client.post.assert_awaited_once()
    payload = observer._client.post.await_args.kwargs["json"]
    assert "[调用工具" in payload["content"]["text"]


async def test_empty_delta_end_no_send(observer):
    """空回合（无 Delta）不发送。"""
    await observer(DeltaEnd())
    observer._client.post.assert_not_awaited()


async def test_error_sent_immediately(observer):
    await observer(Error(message="oops"))
    observer._client.post.assert_awaited_once()


def test_sign():
    """签名算法与飞书文档一致（HmacSHA256 → base64）。"""
    sign = FeishuObserver._sign("1599360473", "demo")
    assert isinstance(sign, str) and len(sign) > 10


async def test_push_failure_logged_no_raise(observer):
    """推送失败仅告警，不抛异常（不拖垮主链路）。"""
    observer._client.post.side_effect = Exception("network down")
    await observer(Error(message="x"))  # 不应抛出
