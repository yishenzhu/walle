"""课程包存储：一门课一个目录，md 正文 + 画布 + course.json 索引。

    data/courses/<course_id>/
        course.json          # 标题、目标、课节索引
        lessons/<lesson>.md  # 课节正文（可含 {{canvas:<id>}} 占位）
        canvas/<canvas>.html # 课程内交互演示

课程包自包含，可直接打包/迁移；工作目录不参与课程文件落盘。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COURSES_PATH = "data/courses"
COURSE_FILE = "course.json"
LESSONS_DIR = "lessons"
CANVAS_DIR = "canvas"
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CANVAS_MARKER = re.compile(r"\{\{canvas:([A-Za-z0-9_-]{1,64})\}\}")


def courses_root(path: str | Path | None = None) -> Path:
    root = Path(path) if path else Path(DEFAULT_COURSES_PATH)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    root.mkdir(parents=True, exist_ok=True)
    return root


def course_id_for(title: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", title).strip("-").lower()[:40]
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
    return f"{safe or 'course'}-{digest}"


def lesson_id_for(title: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", title).strip("-").lower()[:40]
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
    return f"{safe or 'lesson'}-{digest}"


def course_dir(course_id: str, root: str | Path | None = None) -> Path:
    if not SAFE_ID.fullmatch(course_id):
        raise ValueError(f"invalid course id: {course_id}")
    return courses_root(root) / course_id


def lesson_file(course_id: str, lesson_id: str, root: str | Path | None = None) -> Path:
    if not SAFE_ID.fullmatch(lesson_id):
        raise ValueError(f"invalid lesson id: {lesson_id}")
    return course_dir(course_id, root) / LESSONS_DIR / f"{lesson_id}.md"


def canvas_dir(course_id: str, root: str | Path | None = None) -> Path:
    return course_dir(course_id, root) / CANVAS_DIR


def canvas_file(
    course_id: str, canvas_id: str, root: str | Path | None = None
) -> Path:
    if not SAFE_ID.fullmatch(canvas_id):
        raise ValueError(f"invalid canvas id: {canvas_id}")
    return canvas_dir(course_id, root) / f"{canvas_id}.html"


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def load_course(course_id: str, root: str | Path | None = None) -> dict[str, Any] | None:
    try:
        directory = course_dir(course_id, root)
    except ValueError:
        return None
    return read_json(directory / COURSE_FILE)


def save_course(course: dict[str, Any], root: str | Path | None = None) -> None:
    directory = course_dir(str(course["id"]), root)
    directory.mkdir(parents=True, exist_ok=True)
    write_json_atomic(directory / COURSE_FILE, course)


def list_courses(root: str | Path | None = None) -> list[dict[str, Any]]:
    base = courses_root(root)
    result: list[dict[str, Any]] = []
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        course = read_json(entry / COURSE_FILE)
        if course is None:
            continue
        lessons = course.get("lessons") or []
        result.append(
            {
                "id": course.get("id") or entry.name,
                "title": course.get("title") or entry.name,
                "goal": course.get("goal") or "",
                "lesson_count": len(lessons),
                "created_at": course.get("created_at") or 0,
                "updated_at": course.get("updated_at") or 0,
            }
        )
    result.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)
    return result


def create_course(
    title: str,
    goal: str = "",
    lessons: list[str] | None = None,
    course_id: str | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise ValueError("course title is required")
    identifier = course_id or course_id_for(title)
    if not SAFE_ID.fullmatch(identifier):
        raise ValueError(f"invalid course id: {identifier}")

    now = time.time()
    course = load_course(identifier, root)
    if course is None:
        course = {
            "id": identifier,
            "title": title,
            "goal": goal.strip(),
            "created_at": now,
            "updated_at": now,
            "lessons": [],
        }
    else:
        course["title"] = title
        if goal.strip():
            course["goal"] = goal.strip()
        course["updated_at"] = now

    if lessons is not None:
        existing = {item.get("id"): item for item in course.get("lessons") or []}
        entries = []
        for index, lesson_title in enumerate(lessons, 1):
            lesson_title = str(lesson_title).strip()
            if not lesson_title:
                continue
            lesson_id = lesson_id_for(lesson_title)
            previous = existing.get(lesson_id) or {}
            entries.append(
                {
                    "id": lesson_id,
                    "title": lesson_title,
                    "order": index,
                    "file": f"{LESSONS_DIR}/{lesson_id}.md",
                    "updated_at": previous.get("updated_at"),
                    "canvas_ids": previous.get("canvas_ids") or [],
                }
            )
        course["lessons"] = entries

    directory = course_dir(identifier, root)
    (directory / LESSONS_DIR).mkdir(parents=True, exist_ok=True)
    (directory / CANVAS_DIR).mkdir(parents=True, exist_ok=True)
    save_course(course, root)
    return course


def write_lesson(
    course_id: str,
    title: str,
    markdown: str,
    order: int | None = None,
    lesson_id: str | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    course = load_course(course_id, root)
    if course is None:
        raise ValueError(f"course not found: {course_id}")
    title = (title or "").strip()
    if not title:
        raise ValueError("lesson title is required")
    identifier = lesson_id or lesson_id_for(title)
    if not SAFE_ID.fullmatch(identifier):
        raise ValueError(f"invalid lesson id: {identifier}")

    lessons = course.setdefault("lessons", [])
    entry = next((item for item in lessons if item.get("id") == identifier), None)
    if entry is None:
        entry = {
            "id": identifier,
            "title": title,
            "order": order if order is not None else len(lessons) + 1,
            "file": f"{LESSONS_DIR}/{identifier}.md",
            "updated_at": None,
            "canvas_ids": [],
        }
        lessons.append(entry)
    else:
        entry["title"] = title
        if order is not None:
            entry["order"] = order

    markdown = markdown or ""
    entry["file"] = f"{LESSONS_DIR}/{identifier}.md"
    entry["updated_at"] = time.time()
    entry["canvas_ids"] = CANVAS_MARKER.findall(markdown)
    write_text_atomic(lesson_file(course_id, identifier, root), markdown)

    lessons.sort(key=lambda item: item.get("order") or 0)
    course["updated_at"] = entry["updated_at"]
    save_course(course, root)
    return entry


def read_lesson(
    course_id: str, lesson_id: str, root: str | Path | None = None
) -> dict[str, Any] | None:
    course = load_course(course_id, root)
    if course is None:
        return None
    entry = next(
        (item for item in course.get("lessons") or [] if item.get("id") == lesson_id),
        None,
    )
    if entry is None:
        return None
    path = lesson_file(course_id, lesson_id, root)
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError:
        markdown = ""
    return {**entry, "markdown": markdown}
