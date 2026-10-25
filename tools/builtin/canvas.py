"""画布工具：把一页自包含 HTML 渲染到 Web 画布。"""

import hashlib
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
    """画布工具结果：客户端据此渲染入口卡并按 id/version 加载页面。"""

    type: Literal["canvas"] = "canvas"
    canvas_id: str
    title: str
    version: int


def write_canvas(
    title: str,
    html: str,
    canvas_id: str | None,
    root: Path,
) -> CanvasResult | str:
    """把一页 HTML 写入指定画布目录（通用 canvas 与领域插件共用）。"""
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
    html: str,
    canvas_id: str | None = None,
) -> CanvasResult | str:
    """当用户要画图、示意图、公式推导、动画或可交互演示时，渲染 HTML 画布。
    html 必须是完整页面，可内联 CSS/JS 或引用外部 CDN；同标题默认复用画布，
    更新已有画布时传回上次的 canvas_id。"""
    ctx = tool_context.get()
    if ctx is None or not ctx.session_id:
        return "Error: canvas 不可用（当前会话没有会话标识）"
    key = re.sub(r"[^A-Za-z0-9_-]", "_", ctx.session_id)[:64] or "default"
    return write_canvas(title, html, canvas_id, CANVAS_ROOT / key)
