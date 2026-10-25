"""学习插件的 Web 路由：课程列表、阅读页数据、进度、画布与导出。"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from ..plugin import plugin
from ..spec import WebService
from .store import (
    canvas_file,
    course_progress,
    list_courses,
    load_course,
    read_lesson,
    update_progress,
)
from .web_export import build_course_zip

COURSES_ROOT = Path(__file__).resolve().parents[1] / "data" / "courses"
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ProgressUpdate(BaseModel):
    lesson_id: str
    completed: bool | None = None


@plugin(name="learning-web", inject=(WebService,))
async def learning_web(ctx, config) -> None:
    """学习 Web 插件：挂课程路由、静态资源与 UI 清单。"""
    web = ctx.get(WebService)
    static = Path(__file__).with_name("static")
    web.include_router(create_router(web.shell, web.static_dir))
    web.mount_static("/plugins/learning", static)
    web.register_ui(
        {
            "name": "learning",
            "nav": [{"label": "学习", "href": "/courses", "title": "学习空间"}],
            "styles": ["/plugins/learning/course-view.css"],
            "scripts": ["/plugins/learning/views.js"],
        }
    )


PLUGIN = learning_web


def create_router(
    shell: Callable[[], Awaitable[HTMLResponse]],
    static_dir: str | Path,
) -> APIRouter:
    """构造课程路由；`shell` 返回宿主 SPA 页面，`static_dir` 供导出打包。"""
    router = APIRouter()

    @router.get("/courses")
    async def courses_page() -> HTMLResponse:
        # 课程列表是主 Shell 的一个视图，和对话共用同一条 WebSocket。
        return await shell()

    @router.get("/api/courses")
    async def api_courses() -> dict:
        return {"courses": list_courses(COURSES_ROOT)}

    @router.get("/api/courses/{course_id}")
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
        }

    @router.post("/api/courses/{course_id}/progress")
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

    @router.get("/api/courses/{course_id}/lessons/{lesson_id}")
    async def api_lesson(course_id: str, lesson_id: str) -> dict:
        if not SAFE_ID.fullmatch(course_id) or not SAFE_ID.fullmatch(lesson_id):
            raise HTTPException(status_code=404)
        lesson = read_lesson(course_id, lesson_id, COURSES_ROOT)
        if lesson is None:
            raise HTTPException(status_code=404)
        return {"course_id": course_id, "lesson": lesson}

    @router.get("/courses/{course_id}")
    async def course_page(course_id: str) -> HTMLResponse:
        if (
            not SAFE_ID.fullmatch(course_id)
            or load_course(course_id, COURSES_ROOT) is None
        ):
            raise HTTPException(status_code=404)
        # 课程阅读同样是 Shell 视图：右侧主对话保持连接。
        return await shell()

    @router.get("/courses/{course_id}/export")
    async def export_course(course_id: str) -> Response:
        if not SAFE_ID.fullmatch(course_id):
            raise HTTPException(status_code=404)
        try:
            payload = build_course_zip(course_id, COURSES_ROOT, static_dir)
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

    @router.get("/courses/{course_id}/canvas/{canvas_id}")
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

    return router
