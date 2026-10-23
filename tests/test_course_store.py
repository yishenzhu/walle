"""课程包存储测试：课程索引、课节 md、画布关联。"""

import json
from pathlib import Path

import pytest

from ..infra.course_store import (
    CANVAS_MARKER,
    COURSE_FILE,
    create_course,
    lesson_id_for,
    list_courses,
    load_course,
    read_lesson,
    write_lesson,
)


@pytest.fixture
def root(tmp_path):
    return tmp_path / "courses"


def test_create_course_with_lessons(root):
    course = create_course(
        "线性代数",
        goal="掌握向量、矩阵与特征值",
        lessons=["向量", "矩阵", "特征值"],
        root=root,
    )
    assert course["title"] == "线性代数"
    assert [item["title"] for item in course["lessons"]] == ["向量", "矩阵", "特征值"]
    directory = root / course["id"]
    assert (directory / COURSE_FILE).is_file()
    assert (directory / "lessons").is_dir()
    assert (directory / "canvas").is_dir()
    assert list_courses(root)[0]["lesson_count"] == 3


def test_write_and_read_lesson(root):
    course = create_course("傅里叶", lessons=["频域"], root=root)
    markdown = "# 频域\n\n公式 $e^{i\\pi}+1=0$\n\n{{canvas:fourier-demo}}\n"
    entry = write_lesson(
        course["id"],
        "频域",
        markdown,
        root=root,
    )
    assert entry["id"] == lesson_id_for("频域")
    assert entry["canvas_ids"] == ["fourier-demo"]
    assert (root / course["id"] / "lessons" / f"{entry['id']}.md").read_text(
        encoding="utf-8"
    ) == markdown
    loaded = read_lesson(course["id"], entry["id"], root=root)
    assert loaded is not None
    assert loaded["title"] == "频域"
    assert loaded["markdown"] == markdown


def test_write_lesson_updates_existing_entry(root):
    course = create_course("课程", lessons=["第一课"], root=root)
    lesson_id = lesson_id_for("第一课")
    write_lesson(course["id"], "第一课", "old", root=root)
    write_lesson(course["id"], "第一课", "new", lesson_id=lesson_id, root=root)

    lessons = load_course(course["id"], root)["lessons"]
    assert len(lessons) == 1
    assert lessons[0]["id"] == lesson_id
    assert read_lesson(course["id"], lesson_id, root=root)["markdown"] == "new"


def test_update_course_preserves_lesson_metadata(root):
    course = create_course("课程", lessons=["第一课", "第二课"], root=root)
    entry = write_lesson(course["id"], "第一课", "{{canvas:demo}}", root=root)
    updated = create_course(
        "课程（新版）",
        goal="新目标",
        lessons=["第一课", "第三课"],
        course_id=course["id"],
        root=root,
    )
    assert updated["title"] == "课程（新版）"
    assert updated["goal"] == "新目标"
    first = next(item for item in updated["lessons"] if item["id"] == entry["id"])
    assert first["canvas_ids"] == ["demo"]
    assert [item["title"] for item in updated["lessons"]] == ["第一课", "第三课"]


def test_invalid_ids_and_missing_course(root):
    with pytest.raises(ValueError):
        create_course("x", course_id="../escape", root=root)
    with pytest.raises(ValueError):
        write_lesson("missing", "课", "content", root=root)
    assert read_lesson("missing", "lesson", root=root) is None
    assert load_course("../escape", root=root) is None


def test_canvas_marker_is_recorded(root):
    course = create_course("课程", lessons=["课"], root=root)
    entry = write_lesson(
        course["id"],
        "课",
        "{{canvas:a}}\n{{canvas:b}}\n{{canvas:a}}",
        root=root,
    )
    assert entry["canvas_ids"] == ["a", "b", "a"]
    assert CANVAS_MARKER.findall("{{canvas:x}}") == ["x"]


def test_course_json_is_readable(root):
    course = create_course("课程", root=root)
    data = json.loads((Path(root) / course["id"] / COURSE_FILE).read_text(encoding="utf-8"))
    assert data["id"] == course["id"]
    assert data["lessons"] == []
