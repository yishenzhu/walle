"""课程包存储测试：课程索引、课节 md、画布关联。"""

import json
from pathlib import Path

import pytest

from ..learning.store import (
    CANVAS_MARKER,
    COURSE_FILE,
    course_progress,
    create_course,
    lesson_id_for,
    list_courses,
    load_course,
    load_progress,
    read_lesson,
    update_progress,
    write_lesson,
)

OBJECTIVE = "能独立完成本课任务"
PRACTICE = [{"prompt": "做一遍", "answer": "参考答案"}]


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
        objective=OBJECTIVE,
        practice=PRACTICE,
        root=root,
    )
    assert entry["id"] == lesson_id_for("频域")
    assert entry["canvas_ids"] == ["fourier-demo"]
    written = (root / course["id"] / "lessons" / f"{entry['id']}.md").read_text(
        encoding="utf-8"
    )
    assert written.startswith(markdown.rstrip())
    assert "## 练习" in written
    loaded = read_lesson(course["id"], entry["id"], root=root)
    assert loaded is not None
    assert loaded["title"] == "频域"
    assert loaded["markdown"].startswith(markdown.rstrip())


def test_write_lesson_updates_existing_entry(root):
    course = create_course("课程", lessons=["第一课"], root=root)
    lesson_id = lesson_id_for("第一课")
    write_lesson(course["id"], "第一课", "old", objective=OBJECTIVE, practice=PRACTICE, root=root)
    write_lesson(
        course["id"], "第一课", "new", objective=OBJECTIVE, practice=PRACTICE,
        lesson_id=lesson_id, root=root,
    )

    lessons = load_course(course["id"], root)["lessons"]
    assert len(lessons) == 1
    assert lessons[0]["id"] == lesson_id
    assert read_lesson(course["id"], lesson_id, root=root)["markdown"].startswith("new")


def test_update_course_preserves_lesson_metadata(root):
    course = create_course("课程", lessons=["第一课", "第二课"], root=root)
    entry = write_lesson(
        course["id"], "第一课", "{{canvas:demo}}",
        objective=OBJECTIVE, practice=PRACTICE, root=root,
    )
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
        write_lesson("missing", "课", "content", objective=OBJECTIVE, practice=PRACTICE, root=root)
    assert read_lesson("missing", "lesson", root=root) is None
    assert load_course("../escape", root=root) is None


def test_canvas_marker_is_recorded(root):
    course = create_course("课程", lessons=["课"], root=root)
    entry = write_lesson(
        course["id"],
        "课",
        "{{canvas:a}}\n{{canvas:b}}\n{{canvas:a}}",
        objective=OBJECTIVE,
        practice=PRACTICE,
        root=root,
    )
    assert entry["canvas_ids"] == ["a", "b", "a"]
    assert CANVAS_MARKER.findall("{{canvas:x}}") == ["x"]


def test_progress_roundtrip(root):
    course = create_course("进度课程", lessons=["第一课", "第二课"], root=root)
    first = lesson_id_for("第一课")
    second = lesson_id_for("第二课")

    update_progress(course["id"], first, True, root=root)
    progress = load_progress(course["id"], root)
    assert progress["completed"] == [first]
    assert progress["last_lesson"] == first

    update_progress(course["id"], second, True, root=root)
    summary = course_progress(course["id"], root)
    assert summary["completed"] == [first, second]
    assert summary["completed_count"] == 2
    assert summary["last_lesson"] == second

    update_progress(course["id"], first, False, root=root)
    summary = course_progress(course["id"], root)
    assert summary["completed"] == [second]
    assert summary["completed_count"] == 1

    listed = list_courses(root)[0]
    assert listed["completed_count"] == 1
    assert listed["last_lesson"] == first
    assert listed["resume_lesson"] == first

    with pytest.raises(ValueError):
        update_progress(course["id"], "missing", True, root=root)


def test_course_json_is_readable(root):
    course = create_course("课程", root=root)
    data = json.loads((Path(root) / course["id"] / COURSE_FILE).read_text(encoding="utf-8"))
    assert data["id"] == course["id"]
    assert data["lessons"] == []


def test_write_lesson_requires_objective(root):
    course = create_course("课程", lessons=["课"], root=root)
    with pytest.raises(ValueError, match="objective"):
        write_lesson(course["id"], "课", "x", practice=PRACTICE, root=root)


def test_write_lesson_requires_practice(root):
    course = create_course("课程", lessons=["课"], root=root)
    with pytest.raises(ValueError, match="practice"):
        write_lesson(course["id"], "课", "x", objective="能", practice=[], root=root)


def test_practice_requires_prompt_and_answer(root):
    course = create_course("课程", lessons=["课"], root=root)
    with pytest.raises(ValueError, match="answer"):
        write_lesson(
            course["id"], "课", "x", objective="能", practice=[{"prompt": "做一遍"}], root=root
        )


def test_lesson_tail_is_rendered(root):
    course = create_course("课程", lessons=["课"], root=root)
    entry = write_lesson(
        course["id"],
        "课",
        "# 正文",
        objective="能写出一个带超时的调用",
        practice=[{"prompt": "写一个", "answer": "asyncio.wait_for(...)", "check": "不报错"}],
        assessment="不看答案能重写",
        root=root,
    )
    text = read_lesson(course["id"], entry["id"], root=root)["markdown"]
    assert "## 本节目标" in text
    assert "## 练习" in text
    assert "参考答案" in text
    assert "判定标准" in text
    assert "## 自检" in text
    assert entry["objective"] == "能写出一个带超时的调用"
    assert entry["practice"][0]["answer"] == "asyncio.wait_for(...)"


def test_course_outcome_and_learner(root):
    course = create_course(
        "课程",
        can_do=["能用 X 完成 Y", "能解释 Z"],
        learner_level="beginner",
        time_budget="3h",
        root=root,
    )
    assert course["outcome"]["can_do"] == ["能用 X 完成 Y", "能解释 Z"]
    assert course["learner"] == {"level": "beginner", "time_budget": "3h"}


def test_course_can_do_limits(root):
    with pytest.raises(ValueError):
        create_course("课程", can_do=[f"目标{i}" for i in range(6)], root=root)
