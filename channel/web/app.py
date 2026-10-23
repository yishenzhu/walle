"""Web 对话：浏览器 WebSocket ↔ CLI agent 服务端。"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ...infra.course_store import (
    canvas_file,
    course_progress,
    list_courses,
    load_course,
    qa_session_id,
    read_lesson,
    update_progress,
)
from ..transport import HOST, MAX_FRAME, PORT, FrameTooLargeError, JSONLineConn
from .course_export import build_course_zip

logger = logging.getLogger(__name__)
app = FastAPI(title="Walle Web")
INDEX = Path(__file__).with_name("index.html")
COURSES_PAGE = Path(__file__).with_name("courses.html")
COURSE_PAGE = Path(__file__).with_name("course.html")
STATIC = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
CANVAS_ROOT = Path(__file__).resolve().parents[2] / "data" / "canvas"
COURSES_ROOT = Path(__file__).resolve().parents[2] / "data" / "courses"
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ProgressUpdate(BaseModel):
    lesson_id: str
    completed: bool | None = None


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


@app.get("/courses")
async def courses_page() -> HTMLResponse:
    return HTMLResponse(
        COURSES_PAGE.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/courses")
async def api_courses() -> dict:
    return {"courses": list_courses(COURSES_ROOT)}


@app.get("/api/courses/{course_id}")
async def api_course(course_id: str) -> dict:
    if not SAFE_ID.fullmatch(course_id):
        raise HTTPException(status_code=404)
    course = load_course(course_id, COURSES_ROOT)
    if course is None:
        raise HTTPException(status_code=404)
    progress = course_progress(course_id, COURSES_ROOT)
    completed = set(progress["completed"])
    lessons = []
    for item in course.get("lessons") or []:
        loaded = read_lesson(course_id, item["id"], COURSES_ROOT)
        written = bool(loaded and (loaded.get("markdown") or "").strip())
        lessons.append(
            {
                **item,
                "written": written,
                "completed": item["id"] in completed,
            }
        )
    return {
        **course,
        "lessons": lessons,
        "written_count": sum(1 for item in lessons if item["written"]),
        "progress": progress,
        "qa_session_id": qa_session_id(course_id),
    }


@app.post("/api/courses/{course_id}/progress")
async def api_update_progress(course_id: str, payload: ProgressUpdate) -> dict:
    if not SAFE_ID.fullmatch(course_id) or not SAFE_ID.fullmatch(payload.lesson_id):
        raise HTTPException(status_code=404)
    try:
        update_progress(
            course_id,
            payload.lesson_id,
            completed=payload.completed,
            root=COURSES_ROOT,
        )
        progress = course_progress(course_id, COURSES_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"progress": progress}


@app.get("/api/courses/{course_id}/lessons/{lesson_id}")
async def api_lesson(course_id: str, lesson_id: str) -> dict:
    if not SAFE_ID.fullmatch(course_id) or not SAFE_ID.fullmatch(lesson_id):
        raise HTTPException(status_code=404)
    lesson = read_lesson(course_id, lesson_id, COURSES_ROOT)
    if lesson is None:
        raise HTTPException(status_code=404)
    return {"course_id": course_id, "lesson": lesson}


@app.get("/courses/{course_id}")
async def course_page(course_id: str) -> HTMLResponse:
    if not SAFE_ID.fullmatch(course_id) or load_course(course_id, COURSES_ROOT) is None:
        raise HTTPException(status_code=404)
    return HTMLResponse(
        COURSE_PAGE.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/courses/{course_id}/export")
async def export_course(course_id: str) -> Response:
    if not SAFE_ID.fullmatch(course_id):
        raise HTTPException(status_code=404)
    try:
        payload = build_course_zip(course_id, COURSES_ROOT, STATIC)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{course_id}.zip"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/courses/{course_id}/canvas/{canvas_id}")
async def course_canvas(course_id: str, canvas_id: str) -> HTMLResponse:
    if not SAFE_ID.fullmatch(course_id) or not SAFE_ID.fullmatch(canvas_id):
        raise HTTPException(status_code=404)
    path = canvas_file(course_id, canvas_id, COURSES_ROOT)
    if not path.is_file():
        raise HTTPException(status_code=404)
    return HTMLResponse(
        path.read_text(encoding="utf-8"),
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
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
async def chat(
    ws: WebSocket,
    cwd: str | None = None,
    session: str | None = None,
    attach: bool = False,
    course: str | None = None,
    lesson: str | None = None,
    agent: str | None = None,
) -> None:
    await ws.accept()
    if session is not None and not SAFE_ID.fullmatch(session):
        await ws.send_json({"type": "error", "message": "会话 id 非法"})
        await ws.close()
        return
    if course is not None and not SAFE_ID.fullmatch(course):
        await ws.send_json({"type": "error", "message": "课程 id 非法"})
        await ws.close()
        return
    if lesson is not None and not SAFE_ID.fullmatch(lesson):
        await ws.send_json({"type": "error", "message": "课节 id 非法"})
        await ws.close()
        return
    if agent is not None and not SAFE_ID.fullmatch(agent):
        await ws.send_json({"type": "error", "message": "agent 名非法"})
        await ws.close()
        return
    if attach and session is None:
        await ws.send_json({"type": "error", "message": "恢复会话需要 session 参数"})
        await ws.close()
        return
    try:
        working_dir = Path(cwd).expanduser().resolve(strict=True) if cwd else Path.cwd()
    except OSError:
        await ws.send_json(
            {"type": "error", "message": f"工作目录不存在: {cwd or Path.cwd()}"}
        )
        await ws.close()
        return
    if not working_dir.is_dir():
        await ws.send_json({"type": "error", "message": f"不是目录: {working_dir}"})
        await ws.close()
        return

    chat_id = session or f"web-{uuid.uuid4().hex[:12]}"

    async def connect_agent(payload: dict) -> tuple[JSONLineConn, dict | None]:
        reader, writer = await asyncio.open_connection(HOST, PORT, limit=MAX_FRAME)
        conn = JSONLineConn(reader, writer)
        try:
            await conn.send(payload)
            reply = await conn.recv()
        except Exception:
            conn.close()
            await conn.wait_closed()
            raise
        return conn, reply

    link = None
    try:
        hello = {
            "type": "hello",
            "chat_id": chat_id,
            "attach": attach,
            "cwd": str(working_dir),
            "course_id": course,
            "lesson_id": lesson,
            "agent": agent,
            # Web 是人在环审批：不设交互超时，避免用户暂时离开就过期；
            # 浏览器断开会让挂起请求立即失败，页面也有停止按钮兜底。
            "call_timeout": None,
        }
        try:
            link, welcome = await connect_agent(hello)
        except OSError as exc:
            await ws.send_json(
                {"type": "error", "message": f"agent 未启动：{exc}"}
            )
            return

        # 进程重启后内存里没有该会话，但磁盘上可能仍有：换一条新连接
        # 用 attach=false 新建，同 chat_id 会从文件存储读回历史。
        if (
            welcome is not None
            and attach
            and welcome.get("type") == "error"
            and welcome.get("code") == "session_not_found"
        ):
            link.close()
            await link.wait_closed()
            hello = {**hello, "attach": False}
            try:
                link, welcome = await connect_agent(hello)
            except OSError as exc:
                await ws.send_json(
                    {"type": "error", "message": f"agent 未启动：{exc}"}
                )
                return

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
        if link is not None:
            link.close()
            await link.wait_closed()
        try:
            await ws.close()
        except RuntimeError:
            pass
