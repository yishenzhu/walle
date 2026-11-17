"""服务端模型配置：落盘、脱敏、重启续跑。"""

from __future__ import annotations

import json
import os
import stat

import pytest

from ..channel import SessionServer
from ..infra import model_store
from ..learning import resume as resume_mod
from ..learning import store as course_store
from ..spec import ModelConfig

CFG = ModelConfig(api_key="sk-1234567890", base_url="https://8.8.8.8/v1", model="m")


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    target = tmp_path / "data"
    monkeypatch.setattr(model_store, "DATA", target)
    return target


class FakeLink:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, msg):
        self.sent.append(msg)


class FakeSessions:
    def __init__(self):
        self.applied: list[ModelConfig | None] = []

    def apply_model(self, cfg):
        self.applied.append(cfg)
        return 0


def test_save_load_masked(data_dir):
    assert model_store.load_model() is None

    model_store.save_model(CFG)

    assert model_store.load_model() == CFG
    mode = stat.S_IMODE(os.stat(model_store.model_path()).st_mode)
    assert mode == 0o600
    masked = model_store.masked_model()
    assert masked["configured"] is True
    assert "api_key" not in masked
    assert masked["key_tail"] == "7890"


def test_clear_removes_file(data_dir):
    model_store.save_model(CFG)
    model_store.clear_model()

    assert model_store.load_model() is None
    assert model_store.masked_model() == {"configured": False}


def test_corrupt_config_is_ignored(data_dir):
    path = model_store.model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert model_store.load_model() is None


async def test_model_frame_keeps_key_when_omitted(data_dir):
    sessions = FakeSessions()
    server = SessionServer(sessions=sessions)
    link = FakeLink()
    model_store.save_model(CFG)

    await server._save_model_frame(
        link,
        {"model": {"base_url": CFG.base_url, "model": "m2"}},
    )

    saved = model_store.load_model()
    assert saved is not None and saved.api_key == CFG.api_key
    assert saved.model == "m2"
    assert "api_key" not in json.dumps(link.sent[-1])
    assert link.sent[-1]["type"] == "model"


async def test_model_frame_rejects_private_upstream(data_dir):
    server = SessionServer(sessions=FakeSessions())
    link = FakeLink()

    await server._save_model_frame(
        link,
        {"model": {"api_key": "sk-x", "base_url": "http://127.0.0.1:9/v1", "model": "m"}},
    )

    assert link.sent[-1]["type"] == "error"
    assert model_store.load_model() is None


class FakeSession:
    def __init__(self, session_id):
        self.id = session_id
        self.provider = None
        self.started = False

    def set_provider(self, provider):
        self.provider = provider

    async def start(self):
        self.started = True


class FakeManager:
    def __init__(self):
        self.sessions: dict[str, FakeSession] = {}

    def get(self, session_id):
        return self.sessions.get(session_id)

    def create_headless(self, session_id, cwd, agent=None):
        session = FakeSession(session_id)
        self.sessions[session_id] = session
        return session

    def provider_for(self, cfg, session_id=""):
        return ("provider", cfg.model, session_id)


def make_course(root, **fields):
    course = course_store.create_course("测试课程", lessons=["第一课"], root=root)
    path = root / course["id"] / "course.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(fields)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return course["id"]


@pytest.fixture
def courses(tmp_path, monkeypatch):
    root = tmp_path / "courses"
    monkeypatch.setattr(course_store, "courses_root", lambda path=None: root)
    monkeypatch.setattr(resume_mod, "list_courses", lambda: course_store.list_courses(root))
    return root


async def test_resume_rebuilds_headless_session(courses):
    course_id = make_course(
        courses, session_id="web-1", workspace="/opt/walle", status="generating"
    )
    manager = FakeManager()

    count = await resume_mod.resume_pending_courses(manager, CFG)

    assert count == 1
    session = manager.sessions["web-1"]
    assert session.started
    assert session.provider == ("provider", "m", "web-1")
    assert course_store.load_course(course_id, courses)["status"] == "generating"


async def test_resume_skips_failed_pause(courses):
    make_course(
        courses,
        session_id="web-2",
        workspace="/opt/walle",
        status="paused",
        driver={"last_error": "模型不可用，已停止：401"},
    )
    manager = FakeManager()

    assert await resume_mod.resume_pending_courses(manager, CFG) == 0
    assert manager.sessions == {}


async def test_resume_pauses_course_without_session(courses):
    course_id = make_course(courses, status="generating")
    manager = FakeManager()

    assert await resume_mod.resume_pending_courses(manager, CFG) == 0
    loaded = course_store.load_course(course_id, courses)
    assert loaded["status"] == "paused"
    assert "缺少会话信息" in loaded["driver"]["last_error"]
