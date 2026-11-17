"""课程生成驱动器：推进、失败暂停、重启后续跑。"""

from __future__ import annotations

import asyncio

import pytest

from ..learning import driver as driver_mod
from ..learning import store
from ..learning.driver import CourseDriver


class FakeScheduler:
    def __init__(self, on_submit, has_model=True, error=None):
        self._on_submit = on_submit
        self.has_model = has_model
        self.last_error = error
        self.prompts: list[str] = []

    def submit_background(self, content):
        self.prompts.append(content)
        return asyncio.create_task(self._on_submit())


class FakeHistory:
    def __init__(self, total=0):
        self.total = total
        self.cuts: list[int] = []

    async def count(self):
        return self.total

    async def set_projection(self, cut):
        self.cuts.append(cut)


class FakeView:
    def __init__(self, history=None):
        self.history = history


@pytest.fixture
def root(tmp_path):
    return tmp_path / "courses"


def make_course(root, lessons=("第一课", "第二课")):
    return store.create_course("测试课程", lessons=list(lessons), root=root)


async def write_next(course_id, root):
    course = store.load_course(course_id, root)
    pending = store.next_pending_lesson(course, root)
    store.write_lesson(
        course_id,
        pending["title"],
        "正文",
        objective="能复述",
        practice=[{"prompt": "做一遍", "answer": "参考"}],
        root=root,
    )


async def test_driver_advances_lesson_by_lesson(root):
    course = make_course(root)
    scheduler = FakeScheduler(lambda: write_next(course["id"], root))
    driver = CourseDriver(scheduler, "s1", root=root, tick=3600)

    await driver.drive(course["id"])

    loaded = store.load_course(course["id"], root)
    assert loaded["status"] == "done"
    assert len(scheduler.prompts) == 2
    assert "第 1 课" in scheduler.prompts[0]
    assert "本轮只写" in scheduler.prompts[0]
    assert "立即停止" in scheduler.prompts[0]


async def test_driver_pauses_after_three_failures(root, monkeypatch):
    monkeypatch.setattr(driver_mod, "BACKOFF", (0, 0, 0))
    course = make_course(root, lessons=("第一课",))

    async def noop():
        return None

    scheduler = FakeScheduler(noop)
    driver = CourseDriver(scheduler, "s1", root=root)

    await driver.drive(course["id"])

    loaded = store.load_course(course["id"], root)
    assert loaded["status"] == "paused"
    assert "连续失败" in loaded["driver"]["last_error"]
    assert len(scheduler.prompts) == 3


async def test_driver_skips_without_model(root):
    course = make_course(root)
    scheduler = FakeScheduler(lambda: None, has_model=False)
    driver = CourseDriver(scheduler, "s1", root=root)

    await driver.drive(course["id"])

    assert scheduler.prompts == []
    assert store.load_course(course["id"], root)["status"] == "generating"


async def test_scan_resumes_restarted_course(root):
    course = make_course(root, lessons=("第一课",))
    store.mark_course(course["id"], "paused", root=root, last_error="agent restarted")

    scheduler = FakeScheduler(lambda: write_next(course["id"], root))
    driver = CourseDriver(scheduler, "s1", root=root)

    await driver.scan()

    assert store.load_course(course["id"], root)["status"] == "done"
    assert len(scheduler.prompts) == 1


async def test_scan_does_not_resume_failed_course(root):
    course = make_course(root, lessons=("第一课",))
    store.mark_course(course["id"], "paused", root=root, last_error="同一课连续失败 3 次")

    scheduler = FakeScheduler(lambda: None)
    driver = CourseDriver(scheduler, "s1", root=root)

    await driver.scan()

    assert scheduler.prompts == []


async def test_driver_resets_window_before_each_lesson(root):
    course = make_course(root)
    history = FakeHistory(total=7)
    driver = CourseDriver(
        FakeScheduler(lambda: write_next(course["id"], root)),
        "s1",
        view=FakeView(history),
        root=root,
    )

    await driver.drive(course["id"])

    assert history.cuts == [7, 7]


async def test_prompt_carries_full_plan(root):
    course = make_course(root, lessons=("第一课", "第二课"))
    scheduler = FakeScheduler(lambda: write_next(course["id"], root))
    driver = CourseDriver(scheduler, "s1", root=root)

    await driver.drive(course["id"])

    first, second = scheduler.prompts
    assert "[本轮] 1. 第一课" in first
    assert "[待写] 2. 第二课" in first
    assert "[已完成] 1. 第一课" in second
    assert "lesson_id=" in first


async def test_driver_pauses_immediately_on_fatal_error(root, monkeypatch):
    monkeypatch.setattr(driver_mod, "BACKOFF", (0, 0, 0))
    course = make_course(root, lessons=("第一课",))

    async def noop():
        return None

    scheduler = FakeScheduler(noop, error="AuthenticationError: 401 invalid_api_key")
    driver = CourseDriver(scheduler, "s1", root=root)

    await driver.drive(course["id"])

    loaded = store.load_course(course["id"], root)
    assert len(scheduler.prompts) == 1
    assert loaded["status"] == "paused"
    assert "模型不可用" in loaded["driver"]["last_error"]


async def test_driver_retries_on_rate_limit(root, monkeypatch):
    monkeypatch.setattr(driver_mod, "BACKOFF", (0, 0, 0))
    course = make_course(root, lessons=("第一课",))

    async def noop():
        return None

    scheduler = FakeScheduler(noop, error="APIStatusError: 429 rate limit")
    driver = CourseDriver(scheduler, "s1", root=root)

    await driver.drive(course["id"])

    assert len(scheduler.prompts) == 3
    assert store.load_course(course["id"], root)["status"] == "paused"
