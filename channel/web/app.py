"""Web 对话：浏览器 WebSocket ↔ CLI agent 服务端。"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from ..cli.server import HOST, MAX_FRAME, PORT

logger = logging.getLogger(__name__)
app = FastAPI(title="Walle Web")
INDEX = Path(__file__).with_name("index.html")


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX.read_text(encoding="utf-8"))


async def _send(writer: asyncio.StreamWriter, msg: dict) -> None:
    writer.write(json.dumps(msg).encode() + b"\n")
    await writer.drain()


async def _recv(reader: asyncio.StreamReader) -> dict | None:
    line = await reader.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


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

    try:
        await _send(
            writer,
            {
                "type": "hello",
                "chat_id": chat_id,
                "attach": False,
                "cwd": str(Path.cwd()),
            },
        )
        welcome = await _recv(reader)
        if welcome is None:
            await ws.send_json({"type": "error", "message": "agent 未响应"})
            return
        await ws.send_json(welcome)
        if welcome.get("type") == "error":
            return

        async def agent_to_ws() -> None:
            while True:
                msg = await _recv(reader)
                if msg is None:
                    await ws.send_json(
                        {"type": "error", "message": "agent 连接已断开"}
                    )
                    return
                await ws.send_json(msg)

        async def ws_to_agent() -> None:
            while True:
                try:
                    data = await ws.receive_json()
                except (WebSocketDisconnect, RuntimeError):
                    return
                if data.get("type") == "input":
                    await _send(
                        writer,
                        {
                            "type": "input",
                            "content": data.get("content", ""),
                            "chat_id": chat_id,
                        },
                    )
                elif data.get("type") == "reply":
                    await _send(
                        writer,
                        {
                            "type": "reply",
                            "id": data.get("id"),
                            "data": data.get("data"),
                        },
                    )

        tasks = [
            asyncio.create_task(agent_to_ws()),
            asyncio.create_task(ws_to_agent()),
        ]
        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(
                exc, (WebSocketDisconnect, asyncio.CancelledError, OSError)
            ):
                logger.warning("web chat ended: %s", exc)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        try:
            await ws.close()
        except RuntimeError:
            pass
