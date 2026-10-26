"""Canvas tool regression: global store, write path, and structured result."""

import importlib

import pytest

from ..tools.builtin.canvas import CanvasResult

canvas_module = importlib.import_module("walle.tools.builtin.canvas")


@pytest.fixture
def canvas_env(tmp_path, monkeypatch):
    monkeypatch.setattr(canvas_module, "CANVAS_ROOT", tmp_path)
    return tmp_path


async def test_canvas_writes_global_html_and_returns_result(canvas_env):
    result = await canvas_module.canvas(
        "勾股定理", html="<html><body>a²+b²=c²</body></html>"
    )

    assert isinstance(result, CanvasResult)
    assert result.type == "canvas"
    assert result.title == "勾股定理"
    assert result.canvas_id
    assert result.version > 0

    path = canvas_env / f"{result.canvas_id}.html"
    assert path.read_text(encoding="utf-8") == "<html><body>a²+b²=c²</body></html>"


async def test_canvas_same_title_reuses_canvas(canvas_env):
    first = await canvas_module.canvas("推导", html="<html>v1</html>")
    second = await canvas_module.canvas("推导", html="<html>v2</html>")

    assert isinstance(first, CanvasResult)
    assert isinstance(second, CanvasResult)
    assert first.canvas_id == second.canvas_id
    assert second.version > first.version
    path = canvas_env / f"{first.canvas_id}.html"
    assert path.read_text(encoding="utf-8") == "<html>v2</html>"


async def test_canvas_explicit_id_updates_in_place(canvas_env):
    await canvas_module.canvas("旧标题", html="<html>v1</html>", canvas_id="demo")
    result = await canvas_module.canvas(
        "新标题", html="<html>v2</html>", canvas_id="demo"
    )

    assert isinstance(result, CanvasResult)
    assert result.canvas_id == "demo"
    assert result.title == "新标题"
    path = canvas_env / "demo.html"
    assert path.read_text(encoding="utf-8") == "<html>v2</html>"


async def test_canvas_spec_wraps_runtime_page(canvas_env):
    result = await canvas_module.canvas(
        "向量",
        spec='{ title: "向量", draw(k, s) { k.arrow([0,0],[1,1], "accent"); } }',
        canvas_id="spec-demo",
    )
    assert isinstance(result, CanvasResult)
    path = canvas_env / "spec-demo.html"
    text = path.read_text(encoding="utf-8")
    assert "window.CANVAS_SPEC =" in text
    assert "/plugins/learning/canvas-runtime.js" in text


async def test_canvas_rejects_unsafe_id(canvas_env):
    result = await canvas_module.canvas(
        "x", html="<html></html>", canvas_id="../escape"
    )
    assert isinstance(result, str)
    assert result.startswith("Error:")
    assert not any(canvas_env.iterdir())
