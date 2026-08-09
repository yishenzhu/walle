"""render_notification：纯文本渲染，换行是 CLI 表现层职责。"""
import pytest

from walle.channel.channel import CLIChannel
from walle.channel.render import render_notification
from walle.schemas import Delta, DeltaEnd, Error, ToolStart


def test_delta_text():
    assert render_notification(Delta(delta="hi")) == "hi"


def test_delta_end_empty():
    assert render_notification(DeltaEnd()) == ""


def test_tool_start_text():
    text = render_notification(
        ToolStart(tool_name="bash", arguments={"cmd": "ls"}, tool_call_id="1")
    )
    assert "[调用工具" in text


def test_error_text():
    assert "[错误]" in render_notification(Error(message="oops"))


@pytest.mark.asyncio
async def test_cli_notify_renders(capsys):
    """CLI 渲染：Delta 连续不换行，DeltaEnd 换行。"""
    cli = CLIChannel()
    await cli.notify(Delta(delta="你"))
    await cli.notify(Delta(delta="好"))
    await cli.notify(DeltaEnd())
    await cli.notify(ToolStart(tool_name="bash", arguments={"cmd": "ls"}, tool_call_id="1"))
    assert capsys.readouterr().out == "你好\n[调用工具 bash({'cmd': 'ls'})]\n"
