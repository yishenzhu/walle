"""学习插件的 Web 路由：课程列表、阅读页数据、进度、画布与导出。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from ..conf import CANVAS_ROOT as GLOBAL_CANVAS_ROOT
from ..conf import PROJECT_ROOT
from ..plugin import plugin
from ..spec import SAFE_ID, WebService
from .store import (
    DEFAULT_COURSES_PATH,
    canvas_file,
    course_progress,
    html_title,
    lesson_canvas_ids,
    list_courses,
    load_course,
    read_lesson,
    update_progress,
)
from .web_export import build_course_zip

COURSES_ROOT = PROJECT_ROOT / DEFAULT_COURSES_PATH


def _canvas_path(course_id: str, canvas_id: str) -> Path:
    """课程内画布优先，缺省回退全局画布。"""
    path = canvas_file(course_id, canvas_id, COURSES_ROOT)
    return path if path.is_file() else GLOBAL_CANVAS_ROOT / f"{canvas_id}.html"


def _require_ids(*values: str) -> None:
    """路径参数必须是安全 id（防目录穿越），否则 404。"""
    if not all(SAFE_ID.fullmatch(value) for value in values):
        raise HTTPException(status_code=404)


def _canvas_title_for(course_id: str, canvas_id: str) -> str:
    """画布标题：读 HTML 的 h1/title，读不到时回退 id。"""
    path = _canvas_path(course_id, canvas_id)
    try:
        return html_title(path.read_text(encoding="utf-8"), canvas_id)
    except OSError:
        return canvas_id


class ProgressUpdate(BaseModel):
    lesson_id: str
    completed: bool | None = None


@plugin(name="learning-web", inject=(WebService,))
async def learning_web(ctx, config) -> None:
    """学习 Web 插件：挂课程路由、静态资源与 UI 清单。"""
    web = ctx.get(WebService)
    static = Path(__file__).with_name("static")
    version = int(
        max(
            (static / "views.js").stat().st_mtime,
            (static / "course-view.css").stat().st_mtime,
        )
    )
    web.include_router(create_router(web.shell, web.static_dir))
    web.mount_static("/plugins/learning", static)
    web.register_ui(
        {
            "name": "learning",
            "nav": [{"label": "学习", "href": "/courses", "title": "学习空间"}],
            "styles": [f"/plugins/learning/course-view.css?v={version}"],
            "scripts": [f"/plugins/learning/views.js?v={version}"],
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
        _require_ids(course_id)
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
        _require_ids(course_id, payload.lesson_id)
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
        _require_ids(course_id, lesson_id)
        lesson = read_lesson(course_id, lesson_id, COURSES_ROOT)
        if lesson is None:
            raise HTTPException(status_code=404)
        lesson["canvases"] = [
            {"id": canvas_id, "title": _canvas_title_for(course_id, canvas_id)}
            for canvas_id in lesson_canvas_ids(lesson)
        ]
        return {"course_id": course_id, "lesson": lesson}

    @router.get("/courses/{course_id}")
    async def course_page(course_id: str) -> HTMLResponse:
        _require_ids(course_id)
        if load_course(course_id, COURSES_ROOT) is None:
            raise HTTPException(status_code=404)
        # 课程阅读同样是 Shell 视图：右侧主对话保持连接。
        return await shell()

    @router.get("/courses/{course_id}/export")
    async def export_course(course_id: str) -> Response:
        _require_ids(course_id)
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
    async def canvas_page(course_id: str, canvas_id: str) -> HTMLResponse:
        _require_ids(course_id, canvas_id)
        path = _canvas_path(course_id, canvas_id)
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
