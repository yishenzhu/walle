"""render_notification：返回 (文本, end) 元组，换行决策内聚。"""
import pytest

from walle.channel.channel import CLIChannel
from walle.channel.render import render_notification
from walle.schemas import Delta, DeltaEnd, Error, ToolStart


def test_delta_no_newline():
    text, end = render_notification(Delta(delta="hi"))
    assert text == "hi"
    assert end == ""


def test_delta_end_newline_only():
    text, end = render_notification(DeltaEnd())
    assert text == ""
    assert end == "\n"


def test_tool_start_newline():
    text, end = render_notification(
        ToolStart(tool_name="bash", arguments={"cmd": "ls"}, tool_call_id="1")
    )
    assert "[调用工具" in text
    assert end == "\n"


def test_error_newline():
    text, end = render_notification(Error(message="oops"))
    assert "[错误]" in text
    assert end == "\n"


@pytest.mark.asyncio
async def test_cli_notify_renders(capsys):
    """CLI 渲染：Delta 连续不换行，DeltaEnd 换行。"""
    cli = CLIChannel()
    await cli.notify(Delta(delta="你"))
    await cli.notify(Delta(delta="好"))
    await cli.notify(DeltaEnd())
    await cli.notify(ToolStart(tool_name="bash", arguments={"cmd": "ls"}, tool_call_id="1"))
    assert capsys.readouterr().out == "你好\n[调用工具 bash({'cmd': 'ls'})]\n"
