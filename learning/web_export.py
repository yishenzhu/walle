"""课程包导出：生成可离线打开的静态站点 zip。"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from ..tools.builtin.canvas import CANVAS_ROOT as GLOBAL_CANVAS_ROOT
from .store import (
    CANVAS_DIR,
    CANVAS_MARKER,
    COURSE_FILE,
    LESSONS_DIR,
    canvas_file,
    load_course,
    read_lesson,
)

PRIVATE_FIELDS = ("workspace", "session_id")


def course_bundle(
    course_id: str,
    courses_root: str | Path,
) -> dict[str, Any]:
    course = load_course(course_id, courses_root)
    if course is None:
        raise ValueError(f"course not found: {course_id}")

    exported = {k: v for k, v in course.items() if k not in PRIVATE_FIELDS}
    lessons: dict[str, Any] = {}
    for item in exported.get("lessons") or []:
        loaded = read_lesson(course_id, item["id"], courses_root)
        if loaded is not None:
            lessons[item["id"]] = loaded

    canvas_ids: list[str] = []
    for lesson in lessons.values():
        for canvas_id in CANVAS_MARKER.findall(lesson.get("markdown") or ""):
            if canvas_id not in canvas_ids:
                canvas_ids.append(canvas_id)

    canvases: dict[str, str] = {}
    for canvas_id in canvas_ids:
        path = canvas_file(course_id, canvas_id, courses_root)
        if not path.is_file():
            path = GLOBAL_CANVAS_ROOT / f"{canvas_id}.html"
        if path.is_file():
            canvases[canvas_id] = path.read_text(encoding="utf-8")

    return {"course": exported, "lessons": lessons, "canvases": canvases}


def build_course_zip(
    course_id: str,
    courses_root: str | Path,
    static_dir: str | Path,
    template_dir: str | Path | None = None,
) -> bytes:
    bundle = course_bundle(course_id, courses_root)
    static = Path(static_dir)
    template = Path(template_dir or Path(__file__).parent) / "export.html"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", template.read_text(encoding="utf-8"))
        archive.writestr(
            "data.js",
            "window.COURSE_DATA = "
            + json.dumps(bundle, ensure_ascii=False)
            + ";\n",
        )
        archive.writestr(
            COURSE_FILE,
            json.dumps(bundle["course"], ensure_ascii=False, indent=2) + "\n",
        )
        for lesson_id, lesson in bundle["lessons"].items():
            archive.writestr(
                f"{LESSONS_DIR}/{lesson_id}.md",
                lesson.get("markdown") or "",
            )
        for canvas_id, html in bundle["canvases"].items():
            archive.writestr(
                f"{CANVAS_DIR}/{canvas_id}.html",
                html.replace("/plugins/learning/", "../static/"),
            )

        learning_static = Path(__file__).parent / "static"
        archive.write(
            learning_static / "canvas-runtime.js",
            "static/canvas-runtime.js",
        )
        vendor = learning_static / "vendor"
        if vendor.is_dir():
            for path in sorted(vendor.iterdir()):
                if path.is_file():
                    archive.write(path, f"static/vendor/{path.name}")

        for name in ("marked.min.js", "purify.min.js"):
            archive.write(static / name, f"static/{name}")
        katex = static / "katex"
        for path in sorted(katex.rglob("*")):
            if path.is_file():
                archive.write(path, f"static/katex/{path.relative_to(katex)}")

    return buffer.getvalue()
