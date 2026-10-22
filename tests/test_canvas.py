"""Canvas 工具回归：会话隔离、落盘与结构化结果。"""

import importlib

import pytest

from ..infra import tool_context
from ..tools.builtin.canvas import CanvasResult
from .conftest import FakeSession

canvas_module = importlib.import_module("walle.tools.builtin.canvas")


@pytest.fixture
def canvas_env(tmp_path, monkeypatch):
    monkeypatch.setattr(canvas_module, "CANVAS_ROOT", tmp_path)
    token = tool_context.set(FakeSession(session_id="web-abc123"))
    try:
        yield tmp_path
    finally:
        tool_context.reset(token)


async def test_canvas_writes_session_scoped_html_and_returns_result(canvas_env):
    result = await canvas_module.canvas(
        "勾股定理", "<html><body>a²+b²=c²</body></html>"
    )

    assert isinstance(result, CanvasResult)
    assert result.type == "canvas"
    assert result.title == "勾股定理"
    assert result.canvas_id
    assert result.version > 0

    path = canvas_env / "web-abc123" / f"{result.canvas_id}.html"
    assert path.read_text(encoding="utf-8") == "<html><body>a²+b²=c²</body></html>"


async def test_canvas_same_title_reuses_canvas(canvas_env):
    first = await canvas_module.canvas("推导", "<html>v1</html>")
    second = await canvas_module.canvas("推导", "<html>v2</html>")

    assert isinstance(first, CanvasResult)
    assert isinstance(second, CanvasResult)
    assert first.canvas_id == second.canvas_id
    assert second.version > first.version
    path = canvas_env / "web-abc123" / f"{first.canvas_id}.html"
    assert path.read_text(encoding="utf-8") == "<html>v2</html>"


async def test_canvas_explicit_id_updates_in_place(canvas_env):
    await canvas_module.canvas("旧标题", "<html>v1</html>", canvas_id="demo")
    result = await canvas_module.canvas("新标题", "<html>v2</html>", canvas_id="demo")

    assert isinstance(result, CanvasResult)
    assert result.canvas_id == "demo"
    assert result.title == "新标题"
    path = canvas_env / "web-abc123" / "demo.html"
    assert path.read_text(encoding="utf-8") == "<html>v2</html>"


async def test_canvas_rejects_unsafe_id(canvas_env):
    result = await canvas_module.canvas("x", "<html></html>", canvas_id="../escape")
    assert isinstance(result, str)
    assert result.startswith("Error:")
    assert not any(canvas_env.iterdir())


async def test_canvas_requires_session(monkeypatch, tmp_path):
    monkeypatch.setattr(canvas_module, "CANVAS_ROOT", tmp_path)
    token = tool_context.set(FakeSession())
    try:
        result = await canvas_module.canvas("x", "<html></html>")
    finally:
        tool_context.reset(token)
    assert isinstance(result, str)
    assert result.startswith("Error:")
