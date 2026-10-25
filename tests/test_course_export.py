"""课程包导出测试：离线静态站点 zip。"""

import io
import json
import zipfile
from pathlib import Path

import pytest

from ..learning.store import create_course, save_course, write_lesson
from ..learning.web_export import build_course_zip, course_bundle

WEB_DIR = Path(__file__).resolve().parent.parent / "channel" / "web"


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "courses"
    course = create_course(
        "导出课程",
        goal="测试导出",
        lessons=["第一课"],
        root=root,
    )
    course["workspace"] = "/private/workspace"
    course["session_id"] = "private-session"
    save_course(course, root)
    lesson = write_lesson(
        course["id"],
        "第一课",
        "# 第一课\n\n正文\n\n{{canvas:demo}}\n",
        root=root,
    )
    canvas = root / course["id"] / "canvas" / "demo.html"
    canvas.parent.mkdir(parents=True, exist_ok=True)
    canvas.write_text("<html><body>demo</body></html>", encoding="utf-8")
    return root, course, lesson


def test_bundle_is_sanitized(bundle):
    root, course, lesson = bundle
    data = course_bundle(course["id"], root)
    assert data["course"]["title"] == "导出课程"
    assert "workspace" not in data["course"]
    assert "session_id" not in data["course"]
    assert data["lessons"][lesson["id"]]["markdown"].startswith("# 第一课")
    assert data["canvases"]["demo"].startswith("<html>")


def test_build_zip_contains_offline_site(bundle):
    root, course, lesson = bundle
    payload = build_course_zip(course["id"], root, WEB_DIR / "static")
    archive = zipfile.ZipFile(io.BytesIO(payload))
    names = set(archive.namelist())

    assert "index.html" in names
    assert "data.js" in names
    assert "course.json" in names
    assert f"lessons/{lesson['id']}.md" in names
    assert "canvas/demo.html" in names
    assert "static/marked.min.js" in names
    assert "static/purify.min.js" in names
    assert "static/canvas-runtime.js" in names
    assert "static/vendor/jsxgraphcore.js" in names
    assert "static/vendor/jsxgraph.css" in names
    assert "static/katex/katex.min.js" in names
    assert "static/katex/katex.min.css" in names
    assert any(name.startswith("static/katex/fonts/") for name in names)

    index = archive.read("index.html").decode("utf-8")
    assert "static/marked.min.js" in index
    assert "data.js" in index
    data_js = archive.read("data.js").decode("utf-8")
    assert "导出课程" in data_js
    assert "demo" in data_js
    assert "private" not in data_js

    exported = json.loads(archive.read("course.json"))
    assert exported["title"] == "导出课程"
    assert "workspace" not in exported


def test_export_rewrites_canvas_runtime_path(bundle):
    root, course, lesson = bundle
    canvas = root / course["id"] / "canvas" / "spec.html"
    canvas.write_text(
        '<script src="/plugins/learning/canvas-runtime.js"></script>',
        encoding="utf-8",
    )
    payload = build_course_zip(course["id"], root, WEB_DIR / "static")
    archive = zipfile.ZipFile(io.BytesIO(payload))
    html = archive.read("canvas/spec.html").decode("utf-8")
    assert html == '<script src="../static/canvas-runtime.js"></script>'


def test_unknown_course_raises(bundle):
    root, _, _ = bundle
    with pytest.raises(ValueError):
        build_course_zip("missing", root, WEB_DIR / "static")
