"""Web 对话：浏览器 WebSocket ↔ CLI agent 服务端。"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ..transport import HOST, MAX_FRAME, PORT, FrameTooLargeError, JSONLineConn

logger = logging.getLogger(__name__)
app = FastAPI(title="Walle Web")
INDEX = Path(__file__).with_name("index.html")
STATIC = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
CANVAS_ROOT = Path(__file__).resolve().parents[2] / "data" / "canvas"
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse(
        INDEX.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/canvas/{chat_id}/{canvas_id}")
async def canvas_page(chat_id: str, canvas_id: str) -> HTMLResponse:
    if not SAFE_ID.fullmatch(canvas_id):
        raise HTTPException(status_code=404)
    # 与 tools/builtin/canvas.py 的落盘规则保持一致
    key = re.sub(r"[^A-Za-z0-9_-]", "_", chat_id)[:64] or "default"
    path = CANVAS_ROOT / key / f"{canvas_id}.html"
    if not path.is_file():
        raise HTTPException(status_code=404)
    return HTMLResponse(
        path.read_text(encoding="utf-8"),
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.websocket("/ws/chat")
async def chat(ws: WebSocket) -> None:
    await ws.accept()
    chat_id = f"web-{uuid.uuid4().hex[:12]}"
    try:
        reader, writer = await asyncio.open_connection(HOST, PORT, limit=MAX_FRAME)
    except OSError as exc:
        await ws.send_json({"type": "error", "message": f"agent 未启动：{exc}"})
        await ws.close()
        return

    link = JSONLineConn(reader, writer)
    try:
        await link.send(
            {
                "type": "hello",
                "chat_id": chat_id,
                "attach": False,
                "cwd": str(Path.cwd()),
                # Web 是人在环审批：不设交互超时，避免用户暂时离开就过期；
                # 浏览器断开会让挂起请求立即失败，页面也有停止按钮兜底。
                "call_timeout": None,
            }
        )
        welcome = await link.recv()
        if welcome is None:
            await ws.send_json({"type": "error", "message": "agent 未响应"})
            return
        await ws.send_json(welcome)
        if welcome.get("type") == "error":
            return

        async def agent_to_ws() -> None:
            try:
                while (msg := await link.recv()) is not None:
                    await ws.send_json(msg)
                await ws.send_json(
                    {"type": "error", "message": "agent 连接已断开"}
                )
            except FrameTooLargeError:
                await ws.send_json({"type": "error", "message": "agent 帧过大"})

        async def ws_to_agent() -> None:
            while True:
                try:
                    data = await ws.receive_json()
                except (WebSocketDisconnect, RuntimeError):
                    return
                if data.get("type") == "input":
                    await link.send(
                        {
                            "type": "input",
                            "content": data.get("content", ""),
                            "chat_id": chat_id,
                        }
                    )
                elif data.get("type") == "reply":
                    await link.send(
                        {
                            "type": "reply",
                            "id": data.get("id"),
                            "data": data.get("data"),
                        }
                    )
                elif data.get("type") == "cancel":
                    # 服务端读循环支持 cancel（取消当前 run 并回终态），
                    # Web 端补上转发，页面才能中断卡住/过长的执行。
                    await link.send({"type": "cancel"})

        tasks = [
            asyncio.create_task(agent_to_ws()),
            asyncio.create_task(ws_to_agent()),
        ]
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in tasks:
            if task.done() and not task.cancelled() and task.exception() is not None:
                exc = task.exception()
                if not isinstance(exc, (WebSocketDisconnect, OSError)):
                    logger.warning("web chat ended: %s", exc)
    finally:
        link.close()
        await link.wait_closed()
        try:
            await ws.close()
        except RuntimeError:
            pass
