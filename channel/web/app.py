"""Web 通道：浏览器 WebSocket ↔ 会话服务端。"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ...conf import CANVAS_ROOT
from ...plugin import PluginHost
from ...spec import WebService
from ..transport import HOST, MAX_FRAME, PORT, FrameTooLargeError, JSONLineConn
from .plugins import discover_web_plugins

logger = logging.getLogger(__name__)
app = FastAPI(title="Walle Web")
INDEX = Path(__file__).with_name("index.html")
STATIC = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse(
        INDEX.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


async def query_agent(frame: dict, timeout: float = 5.0) -> dict:
    """向 agent 服务端发一个只读浏览帧并取回结果。"""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(HOST, PORT, limit=MAX_FRAME), timeout
        )
    except (OSError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail="agent 未启动") from exc
    link = JSONLineConn(reader, writer)
    try:
        await link.send(frame)
        reply = await asyncio.wait_for(link.recv(), timeout)
    except (OSError, ConnectionError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail="agent 未响应") from exc
    finally:
        link.close()
        await link.wait_closed()
    if reply is None:
        raise HTTPException(status_code=503, detail="agent 未响应")
    if reply.get("type") == "error":
        raise HTTPException(
            status_code=400,
            detail=reply.get("message") or reply.get("code") or "agent error",
        )
    return reply


@app.get("/api/workspaces")
async def api_workspaces() -> dict:
    reply = await query_agent({"type": "list_workspaces"})
    return {
        "current": reply.get("current") or str(Path.cwd()),
        "workspaces": reply.get("workspaces") or [],
    }


@app.get("/api/sessions")
async def api_sessions(cwd: str = "") -> dict:
    if not cwd:
        raise HTTPException(status_code=400, detail="cwd 不能为空")
    try:
        target = Path(cwd).expanduser().resolve(strict=True)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"目录不存在: {cwd}") from exc
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"不是目录: {cwd}")
    reply = await query_agent({"type": "list_sessions", "cwd": str(target)})
    return {"cwd": str(target), "sessions": reply.get("sessions") or []}


class WebHost:
    """Web 宿主能力实现：把插件路由挂到 FastAPI 应用。"""

    def __init__(self, fastapi_app: FastAPI, shell, static_dir: Path) -> None:
        self._app = fastapi_app
        self._shell = shell
        self._static_dir = static_dir
        self._ui: list[dict] = []

    @property
    def shell(self):
        return self._shell

    @property
    def static_dir(self) -> Path:
        return self._static_dir

    @property
    def ui_plugins(self) -> list[dict]:
        return list(self._ui)

    def include_router(self, router) -> None:
        self._app.include_router(router)

    def mount_static(self, prefix: str, directory: Path) -> None:
        self._app.mount(
            prefix,
            StaticFiles(directory=directory),
            name=f"plugin-{prefix.strip('/').replace('/', '-')}",
        )

    def register_ui(self, manifest: dict) -> None:
        self._ui.append(manifest)


async def install_web_plugins() -> None:
    """启动 Web 插件宿主：注册 WebService，加载包内声明的 Web 插件（幂等）。"""
    if getattr(app.state, "plugin_host", None) is not None:
        return
    web_host = WebHost(app, index, STATIC)
    host = PluginHost()
    await host.root.register(WebService, web_host)
    await host.launch(discover_web_plugins())
    app.state.plugin_host = host
    app.state.web_host = web_host


@app.get("/api/web/plugins")
async def api_web_plugins() -> dict:
    """返回 Web 插件 UI 清单，供 Shell 动态加载导航与脚本。"""
    web_host = getattr(app.state, "web_host", None)
    return {"plugins": web_host.ui_plugins if web_host is not None else []}


@app.get("/canvas/{session_id}/{canvas_id}")
async def canvas_page(session_id: str, canvas_id: str) -> HTMLResponse:
    if not SAFE_ID.fullmatch(canvas_id):
        raise HTTPException(status_code=404)
    # 新画布是全局资源；旧画布仍按 session 目录回退
    key = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)[:64] or "default"
    path = CANVAS_ROOT / f"{canvas_id}.html"
    if not path.is_file():
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


async def _reject(ws: WebSocket, message: str) -> None:
    """握手期拒绝：回错误帧并关连接。"""
    await ws.send_json({"type": "error", "message": message})
    await ws.close()


@app.websocket("/ws/chat")
async def chat(
    ws: WebSocket,
    cwd: str | None = None,
    session: str | None = None,
    attach: bool = False,
    agent: str | None = None,
) -> None:
    await ws.accept()
    if session is not None and not SAFE_ID.fullmatch(session):
        await _reject(ws, "会话 id 非法")
        return
    if agent is not None and not SAFE_ID.fullmatch(agent):
        await _reject(ws, "agent 名非法")
        return
    if attach and session is None:
        await _reject(ws, "恢复会话需要 session 参数")
        return
    try:
        working_dir = Path(cwd).expanduser().resolve(strict=True) if cwd else Path.cwd()
    except OSError:
        await _reject(ws, f"工作目录不存在: {cwd or Path.cwd()}")
        return
    if not working_dir.is_dir():
        await _reject(ws, f"不是目录: {working_dir}")
        return

    session_id = session or f"web-{uuid.uuid4().hex[:12]}"

    async def open_agent(payload: dict) -> tuple[JSONLineConn, dict | None] | None:
        """连上 agent 并发握手帧；连不上时回错误帧并返回 None。"""
        try:
            reader, writer = await asyncio.open_connection(
                HOST, PORT, limit=MAX_FRAME
            )
        except OSError as exc:
            await ws.send_json(
                {"type": "error", "message": f"agent 未启动：{exc}"}
            )
            return None
        conn = JSONLineConn(reader, writer)
        try:
            await conn.send(payload)
            return conn, await conn.recv()
        except Exception:
            conn.close()
            await conn.wait_closed()
            raise

    link = None
    try:
        hello = {
            "type": "hello",
            "session_id": session_id,
            "attach": attach,
            "cwd": str(working_dir),
            "agent": agent,
            # Web 是人在环审批：不设交互超时，避免用户暂时离开就过期；
            # 浏览器断开会让挂起请求立即失败，页面也有停止按钮兜底。
            "call_timeout": None,
        }
        opened = await open_agent(hello)
        if opened is None:
            return
        link, welcome = opened

        # 进程重启后内存里没有该会话，但磁盘上可能仍有：换一条新连接
        # 用 attach=false 新建，同 session_id 会从文件存储读回历史。
        if (
            welcome is not None
            and attach
            and welcome.get("type") == "error"
            and welcome.get("code") == "session_not_found"
        ):
            link.close()
            await link.wait_closed()
            hello = {**hello, "attach": False}
            opened = await open_agent(hello)
            if opened is None:
                return
            link, welcome = opened

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
                kind = data.get("type")
                if kind == "input.create":
                    await link.send(
                        {
                            "type": kind,
                            "content": data.get("content") or "",
                            "session_id": session_id,
                        }
                    )
                elif kind == "reply":
                    await link.send(
                        {
                            "type": kind,
                            "id": data.get("id"),
                            "data": data.get("data"),
                        }
                    )
                elif kind == "run.cancel":
                    await link.send({"type": kind})
                elif kind == "session.set_agent":
                    await link.send(
                        {"type": kind, "agent": data.get("agent") or ""}
                    )
                elif kind == "session.set_metadata":
                    # 校验交给 server（与直连语义一致），web 只做转发
                    await link.send({"type": kind, "values": data.get("values")})

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
        if link is not None:
            link.close()
            await link.wait_closed()
        try:
            await ws.close()
        except RuntimeError:
            pass
