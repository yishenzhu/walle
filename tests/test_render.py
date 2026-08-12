"""CLI 客户端渲染：Delta 流式 / 工具事件 / 错误。"""
import pytest

from walle.channel.cli import CLIClient


@pytest.mark.asyncio
async def test_cli_stream_render(capsys):
    """Delta 连续不换行，DeltaEnd 换行（AI 回答结束）。"""
    CLIClient.render_notification({"type": "delta", "delta": "你"})
    CLIClient.render_notification({"type": "delta", "delta": "好"})
    CLIClient.render_notification({"type": "delta_end"})
    assert capsys.readouterr().out == "你好\n"


@pytest.mark.asyncio
async def test_cli_tool_event_render(capsys):
    """工具事件独立成行，带 icon 与颜色。"""
    CLIClient.render_notification({"type": "tool_start", "tool_name": "bash", "arguments": {"cmd": "ls"}})
    CLIClient.render_notification({"type": "error", "message": "oops"})
    out = capsys.readouterr().out
    assert "🔧 bash" in out
    assert "⚠️ oops" in out
