"""Canvas tool: render an interactive page into the web canvas."""

import hashlib
import html as html_module
import re
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ...infra import tool_context

CANVAS_ROOT = Path(__file__).resolve().parents[2] / "data" / "canvas"
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class CanvasResult(BaseModel):
    """Canvas result: id/title/version for the client entry card."""

    type: Literal["canvas"] = "canvas"
    canvas_id: str
    title: str
    version: int


def spec_html(title: str, spec: str) -> str:
    """Wrap a runtime spec in a minimal HTML page."""
    safe_title = html_module.escape(title or "Canvas")
    body = spec.strip()
    if body.startswith("window.CANVAS_SPEC"):
        body = body.split("=", 1)[1].strip()
    body = body.rstrip(";").replace("</script", "<\\/script")
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN"><head><meta charset="utf-8">'
        f"<title>{safe_title}</title></head><body>\n"
        "<script>\n"
        f"window.CANVAS_SPEC = {body};\n"
        "</script>\n"
        '<script src="/plugins/learning/canvas-runtime.js"></script>\n'
        "</body></html>\n"
    )


def create_canvas(
    title: str,
    spec: str | None,
    html: str | None,
    canvas_id: str | None,
    root: Path,
) -> CanvasResult | str:
    """Validate, wrap, and write one canvas page."""
    if bool(spec) == bool(html) or (spec and "draw" not in spec):
        return "Error: provide one of spec (with draw) or html"
    if spec:
        html = spec_html(title, spec)

    title = (title or "").strip() or "画布"
    if not html or not html.strip():
        return "Error: html 不能为空"
    if canvas_id is None:
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", title).strip("-").lower()[:40]
        digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
        canvas_id = f"{slug or 'canvas'}-{digest}"
    elif not SAFE_ID.fullmatch(canvas_id):
        return "Error: canvas_id 只能包含字母、数字、- 和 _（1-64 位）"

    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{canvas_id}.html"
    temp = root / f".{canvas_id}.{uuid.uuid4().hex}.tmp"
    temp.write_text(html, encoding="utf-8")
    temp.replace(path)
    return CanvasResult(
        canvas_id=canvas_id,
        title=title,
        version=time.time_ns(),
    )


async def canvas(
    title: str,
    spec: str | None = None,
    html: str | None = None,
    canvas_id: str | None = None,
) -> CanvasResult | str:
    """Create or update an interactive canvas.
    Prefer `spec`; see the `canvas-spec` skill for the schema and kit.
    Use `html` only for custom pages; pass `canvas_id` to update."""
    ctx = tool_context.get()
    if ctx is None or not ctx.session_id:
        return "Error: canvas 不可用（当前会话没有会话标识）"
    key = re.sub(r"[^A-Za-z0-9_-]", "_", ctx.session_id)[:64] or "default"
    return create_canvas(title, spec, html, canvas_id, CANVAS_ROOT / key)
