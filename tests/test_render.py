"""CLIChannel 渲染：AI 回答流式不换行，回答结束换行，工具事件独立成行。"""
import pytest

from walle.channel.channel import CLIChannel
from walle.schemas import Delta, DeltaEnd, Error, ToolStart


@pytest.mark.asyncio
async def test_cli_stream_render(capsys):
    """Delta 连续不换行，DeltaEnd 换行（AI 回答结束）。"""
    cli = CLIChannel()
    await cli.notify(Delta(delta="你"))
    await cli.notify(Delta(delta="好"))
    await cli.notify(DeltaEnd())
    assert capsys.readouterr().out == "你好\n"


@pytest.mark.asyncio
async def test_cli_tool_event_render(capsys):
    """工具事件独立成行。"""
    cli = CLIChannel()
    await cli.notify(ToolStart(tool_name="bash", arguments={"cmd": "ls"}, tool_call_id="1"))
    await cli.notify(Error(message="oops"))
    out = capsys.readouterr().out
    assert "[调用工具 bash({'cmd': 'ls'})]\n" in out
    assert "[错误] oops\n" in out
