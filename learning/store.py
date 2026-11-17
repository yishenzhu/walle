"""课程包存储：一门课一个目录，md 正文 + 画布 + course.json 索引。"""

from __future__ import annotations

import hashlib
import re
import time
from html import unescape
from pathlib import Path
from typing import Any

from ..conf import HOME
from ..infra.jsonio import read_json, write_json_atomic, write_text_atomic
from ..spec import SAFE_ID

DEFAULT_COURSES_PATH = "data/courses"
COURSE_FILE = "course.json"
LESSONS_DIR = "lessons"
CANVAS_DIR = "canvas"
PROGRESS_FILE = "progress.json"
CANVAS_MARKER = re.compile(r"\{\{canvas:([A-Za-z0-9_-]{1,64})\}\}")
MAX_OUTCOMES = 5
MAX_PRACTICES = 5


def validate_outcomes(can_do: list[str] | None) -> list[str]:
    if can_do is None:
        return []
    items = [str(item).strip() for item in can_do if str(item).strip()]
    if not items:
        raise ValueError("can_do 至少需要一条可验证的能力目标")
    if len(items) > MAX_OUTCOMES:
        raise ValueError(f"can_do 最多 {MAX_OUTCOMES} 条")
    return items


def validate_practices(practice: list[dict] | None) -> list[dict]:
    if not practice:
        raise ValueError("practice 至少需要一个练习")
    if len(practice) > MAX_PRACTICES:
        raise ValueError(f"practice 最多 {MAX_PRACTICES} 个")
    cleaned = []
    for index, item in enumerate(practice, 1):
        prompt = str((item or {}).get("prompt") or "").strip()
        answer = str((item or {}).get("answer") or "").strip()
        if not prompt or not answer:
            raise ValueError(f"第 {index} 个练习缺少 prompt 或 answer")
        cleaned.append(
            {
                "type": str((item or {}).get("type") or "task").strip(),
                "prompt": prompt,
                "answer": answer,
                "check": str((item or {}).get("check") or "").strip(),
            }
        )
    return cleaned


def render_lesson_tail(objective: str, practice: list[dict], assessment: str) -> str:
    """把目标、练习、自检渲染成 Markdown，附在正文之后。"""
    lines = ["", "---", "", "## 本节目标", "", objective.strip(), "", "## 练习", ""]
    for index, item in enumerate(practice, 1):
        lines += [f"### 练习 {index}", "", item["prompt"], ""]
        if item.get("check"):
            lines += [f"> 判定标准：{item['check']}", ""]
        lines += [
            "<details><summary>参考答案（先自己做）</summary>",
            "",
            item["answer"],
            "",
            "</details>",
            "",
        ]
    if assessment.strip():
        lines += ["## 自检", "", assessment.strip(), ""]
    return "\n".join(lines).rstrip() + "\n"
H1_TITLE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
HTML_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
HTML_TAG = re.compile(r"<[^>]+>")


def courses_root(path: str | Path | None = None) -> Path:
    root = Path(path) if path else Path(DEFAULT_COURSES_PATH)
    if not root.is_absolute():
        root = HOME / root
    root.mkdir(parents=True, exist_ok=True)
    return root


def _slug_id(title: str, fallback: str) -> str:
    """标题 → 稳定 id：可读前缀 + 内容摘要，非法字符归一为连字符。"""
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", title).strip("-").lower()[:40]
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
    return f"{safe or fallback}-{digest}"


def course_id_for(title: str) -> str:
    return _slug_id(title, "course")


def lesson_id_for(title: str) -> str:
    return _slug_id(title, "lesson")


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


def lesson_canvas_ids(lesson: dict[str, Any]) -> list[str]:
    """一课的画布 id：正文标记 + 元数据记录，去重保序。"""
    ids: list[str] = []
    sources = [
        *CANVAS_MARKER.findall(lesson.get("markdown") or ""),
        *(lesson.get("canvas_ids") or []),
    ]
    for canvas_id in sources:
        if canvas_id not in ids:
            ids.append(canvas_id)
    return ids


def outline_lines(
    course: dict[str, Any], current_lesson_id: str | None = None
) -> list[str]:
    """课程大纲：一行一课，当前课加标记。"""
    lines = ["Lessons:"]
    for item in course.get("lessons") or []:
        marker = " (current)" if item.get("id") == current_lesson_id else ""
        lines.append(
            f"- {item.get('order') or '?'}. {item.get('title')} "
            f"[{item.get('id')}]{marker}"
        )
    return lines


def html_title(html: str, fallback: str) -> str:
    """Extract a canvas title from HTML, falling back to `fallback`."""
    match = H1_TITLE.search(html) or HTML_TITLE.search(html)
    if match is None:
        return fallback
    text = unescape(HTML_TAG.sub("", match.group(1)))
    return " ".join(text.split()) or fallback


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


def progress_path(course_id: str, root: str | Path | None = None) -> Path:
    return course_dir(course_id, root) / PROGRESS_FILE


def load_progress(
    course_id: str, root: str | Path | None = None
) -> dict[str, Any]:
    """读取学习进度；缺失/损坏时返回空进度。"""
    try:
        data = read_json(progress_path(course_id, root)) or {}
    except ValueError:
        data = {}
    completed = [item for item in data.get("completed") or [] if isinstance(item, str)]
    last = data.get("last_lesson")
    return {
        "completed": completed,
        "last_lesson": last if isinstance(last, str) else None,
        "updated_at": data.get("updated_at") or 0,
    }


def save_progress(
    course_id: str,
    progress: dict[str, Any],
    root: str | Path | None = None,
) -> None:
    write_json_atomic(progress_path(course_id, root), progress)


def course_progress(
    course_id: str, root: str | Path | None = None
) -> dict[str, Any]:
    """课程进度摘要：只统计当前仍存在的课节，保持课程顺序。"""
    course = load_course(course_id, root)
    if course is None:
        raise ValueError(f"course not found: {course_id}")
    lessons = course.get("lessons") or []
    valid = [item.get("id") for item in lessons if item.get("id")]
    raw = load_progress(course_id, root)
    done = set(raw["completed"])
    completed = [lesson_id for lesson_id in valid if lesson_id in done]
    last = raw["last_lesson"] if raw["last_lesson"] in valid else None
    return {
        "completed": completed,
        "completed_count": len(completed),
        "total": len(valid),
        "last_lesson": last,
        "updated_at": raw["updated_at"],
    }


def update_progress(
    course_id: str,
    lesson_id: str,
    completed: bool | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """更新完成状态与上次课节；completed=None 只更新 last_lesson。"""
    course = load_course(course_id, root)
    if course is None:
        raise ValueError(f"course not found: {course_id}")
    lessons = course.get("lessons") or []
    valid = [item.get("id") for item in lessons if item.get("id")]
    if lesson_id not in valid:
        raise ValueError(f"lesson not found: {lesson_id}")

    progress = load_progress(course_id, root)
    done = set(progress["completed"])
    if completed is True:
        done.add(lesson_id)
    elif completed is False:
        done.discard(lesson_id)
    progress["completed"] = [item for item in valid if item in done]
    progress["last_lesson"] = lesson_id
    progress["updated_at"] = time.time()
    save_progress(course_id, progress, root)
    return progress


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
        identifier = course.get("id") or entry.name
        progress = load_progress(identifier, root)
        valid = {item.get("id") for item in lessons if item.get("id")}
        completed = {item for item in progress["completed"] if item in valid}
        last_lesson = (
            progress["last_lesson"] if progress["last_lesson"] in valid else None
        )
        resume = last_lesson if last_lesson and last_lesson not in completed else None
        if resume is None:
            resume = next(
                (item.get("id") for item in lessons if item.get("id") not in completed),
                last_lesson,
            )
        result.append(
            {
                "id": identifier,
                "title": course.get("title") or entry.name,
                "goal": course.get("goal") or "",
                "lesson_count": len(lessons),
                "completed_count": len(completed),
                "last_lesson": last_lesson,
                "resume_lesson": resume,
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
    can_do: list[str] | None = None,
    learner_level: str = "",
    time_budget: str = "",
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

    outcomes = validate_outcomes(can_do)
    if outcomes:
        course["outcome"] = {"can_do": outcomes}
    if learner_level.strip() or time_budget.strip():
        course["learner"] = {
            "level": learner_level.strip(),
            "time_budget": time_budget.strip(),
        }

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
    objective: str = "",
    practice: list[dict] | None = None,
    assessment: str = "",
    minutes: int | None = None,
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
    objective = (objective or "").strip()
    if not objective:
        raise ValueError("objective 不能为空：写清这节课学完能做什么")
    practices = validate_practices(practice)
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

    body = (markdown or "").rstrip()
    body += "\n" + render_lesson_tail(objective, practices, assessment)
    entry["file"] = f"{LESSONS_DIR}/{identifier}.md"
    entry["updated_at"] = time.time()
    entry["objective"] = objective
    entry["practice"] = practices
    entry["assessment"] = (assessment or "").strip()
    if minutes is not None:
        entry["minutes"] = minutes
    entry["canvas_ids"] = CANVAS_MARKER.findall(body)
    markdown = body
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
