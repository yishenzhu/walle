"""Web 通道：浏览器 WebSocket ↔ 会话服务端。"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ...conf import CANVAS_ROOT
from ...plugin import PluginHost
from ...spec import SAFE_ID, ModelConfig, WebService
from ..frames import ErrorCode, Frame
from ..transport import CONNECT_HOST, MAX_FRAME, PORT, FrameTooLargeError, JSONLineConn
from .plugins import discover_web_plugins

logger = logging.getLogger(__name__)
app = FastAPI(title="Walle Web")
INDEX = Path(__file__).with_name("index.html")
STATIC = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# 会话级模型配置：内存暂存，握手后即丢弃，不落盘、不写日志。
PENDING_MODELS: dict[str, tuple[float, dict]] = {}
MODEL_TTL = 300.0


def allow_private_upstream() -> bool:
    return os.getenv("WALLE_ALLOW_PRIVATE_UPSTREAM", "0").lower() in ("1", "true", "yes", "on")


def private_host(host: str) -> bool:
    """解析主机名并判断是否指向内网/回环/链路本地地址。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return True
    return False


async def validate_upstream(base_url: str) -> None:
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(400, "base_url 必须是 http(s) 地址")
    if allow_private_upstream():
        return
    if await asyncio.to_thread(private_host, parts.hostname):
        raise HTTPException(400, "base_url 指向内网地址，未启用 WALLE_ALLOW_PRIVATE_UPSTREAM")


def pending_model(session_id: str) -> dict | None:
    now = time.monotonic()
    for key, (born, _) in list(PENDING_MODELS.items()):
        if now - born > MODEL_TTL:
            PENDING_MODELS.pop(key, None)
    entry = PENDING_MODELS.get(session_id)
    return entry[1] if entry else None


@app.post("/api/session/{session_id}/model")
async def register_model(session_id: str, payload: dict) -> dict[str, str]:
    """登记会话级模型配置：只在内存保留到握手，用于 BYOK。"""
    if not SAFE_ID.fullmatch(session_id):
        raise HTTPException(400, "会话 id 非法")
    try:
        cfg = ModelConfig.model_validate(payload)
    except Exception:
        raise HTTPException(400, "api_key / base_url / model 都必须是非空字符串") from None
    await validate_upstream(cfg.base_url)
    PENDING_MODELS[session_id] = (time.monotonic(), cfg.model_dump())
    return {"status": "ok"}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse(
        INDEX.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


BROWSE_TIMEOUT = 5.0


async def _open_link(payload: dict, timeout: float | None = None) -> JSONLineConn:
    """连上 agent 并发首帧；连接/写失败时关闭并抛出。"""
    connect = asyncio.open_connection(CONNECT_HOST, PORT, limit=MAX_FRAME)
    reader, writer = (
        await connect if timeout is None else await asyncio.wait_for(connect, timeout)
    )
    link = JSONLineConn(reader, writer)
    try:
        await link.send(payload)
    except Exception:
        link.close()
        await link.wait_closed()
        raise
    return link


async def query_agent(frame: dict, timeout: float = BROWSE_TIMEOUT) -> dict:
    """向 agent 服务端发一个只读浏览帧并取回结果。"""
    try:
        link = await _open_link(frame, timeout)
    except (OSError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail="agent 未启动") from exc
    try:
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
    await ws.send_json({"type": Frame.ERROR, "message": message})
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
            conn = await _open_link(payload)
        except OSError as exc:
            await ws.send_json(
                {"type": Frame.ERROR, "message": f"agent 未启动：{exc}"}
            )
            return None
        try:
            return conn, await conn.recv()
        except Exception:
            conn.close()
            await conn.wait_closed()
            raise

    link = None
    try:
        hello = {
            "type": Frame.HELLO,
            "session_id": session_id,
            "model": pending_model(session_id),
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
            and welcome.get("code") == ErrorCode.SESSION_NOT_FOUND
        ):
            link.close()
            await link.wait_closed()
            hello = {**hello, "attach": False}
            opened = await open_agent(hello)
            if opened is None:
                return
            link, welcome = opened

        PENDING_MODELS.pop(session_id, None)
        if welcome is None:
            await ws.send_json({"type": Frame.ERROR, "message": "agent 未响应"})
            return
        await ws.send_json(welcome)
        if welcome.get("type") == "error":
            return

        async def agent_to_ws() -> None:
            try:
                while (msg := await link.recv()) is not None:
                    await ws.send_json(msg)
                await ws.send_json(
                    {"type": Frame.ERROR, "message": "agent 连接已断开"}
                )
            except FrameTooLargeError:
                await ws.send_json({"type": Frame.ERROR, "message": "agent 帧过大"})

        async def ws_to_agent() -> None:
            while True:
                try:
                    data = await ws.receive_json()
                except (WebSocketDisconnect, RuntimeError):
                    return
                kind = data.get("type")
                if kind == Frame.INPUT_CREATE:
                    await link.send(
                        {
                            "type": kind,
                            "content": data.get("content") or "",
                            "session_id": session_id,
                        }
                    )
                elif kind == Frame.REPLY:
                    await link.send(
                        {
                            "type": kind,
                            "id": data.get("id"),
                            "data": data.get("data"),
                        }
                    )
                elif kind == Frame.RUN_CANCEL:
                    await link.send({"type": kind})
                elif kind == Frame.SESSION_SET_AGENT:
                    await link.send(
                        {"type": kind, "agent": data.get("agent") or ""}
                    )
                elif kind == Frame.SESSION_SET_MODEL:
                    await link.send(
                        {"type": kind, "model": data.get("model")}
                    )
                elif kind == Frame.SESSION_SET_METADATA:
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
