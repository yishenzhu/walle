"""Web 对话：浏览器 WebSocket ↔ CLI agent 服务端。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from ..transport import HOST, MAX_FRAME, PORT, FrameTooLargeError, JSONLineConn

logger = logging.getLogger(__name__)
app = FastAPI(title="Walle Web")
INDEX = Path(__file__).with_name("index.html")


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX.read_text(encoding="utf-8"))


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
