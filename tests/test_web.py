"""Web 对话页与 WebSocket 桥接回归。"""

import asyncio
import importlib
import io
import queue
import threading
import time
import zipfile
from urllib.parse import quote

from fastapi.testclient import TestClient

from ..channel.transport import JSONLineConn
from ..learning import web as learning_web
from ..learning.store import create_course, write_lesson

web = importlib.import_module("walle.channel.web.app")


class FakeAgent:
    """最小 JSON-line agent 服务端：welcome → 审批 call / 终态 notify。"""

    def __init__(self, attach_error: bool = False) -> None:
        self.frames: queue.Queue[dict] = queue.Queue()
        self.hello: dict | None = None
        self.hello_frames: list[dict] = []
        self.attach_error = attach_error
        self.port = 0
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> int:
        self._thread.start()
        assert self._ready.wait(5), "fake agent failed to start"
        return self.port

    def stop(self) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=5)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop = asyncio.Event()
        try:
            self._loop.run_until_complete(self._main())
        finally:
            self._loop.close()

    async def _main(self) -> None:
        server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = server.sockets[0].getsockname()[1]
        self._ready.set()
        try:
            async with server:
                await self._stop.wait()
        finally:
            server.close()

    async def _handle(self, reader, writer) -> None:
        conn = JSONLineConn(reader, writer)
        try:
            hello = await conn.recv()
            if hello is None:
                return
            self.hello = hello
            self.hello_frames.append(hello)
            if self.attach_error and hello.get("attach"):
                # 与真实 SessionServer 一致：attach 失败后回 error 并关闭连接，
                # 客户端需要新开一条连接再 create。
                await conn.send(
                    {
                        "type": "error",
                        "code": "session_not_found",
                        "message": "missing",
                    }
                )
                return
            await conn.send({"type": "welcome", "session_id": hello.get("session_id")})
            while (msg := await conn.recv()) is not None:
                self.frames.put(msg)
                kind = msg.get("type")
                if kind == "input.create":
                    await conn.send(
                        {
                            "type": "call",
                            "id": "req-1",
                            "data": {
                                "type": "approval",
                                "tool_name": "bash",
                                "arguments": {"cmd": "echo hi"},
                                "tool_call_id": "tc1",
                            },
                        }
                    )
                elif kind == "reply":
                    await conn.send(
                        {"type": "notify", "data": {"type": "delta_end"}}
                    )
                elif kind == "run.cancel":
                    await conn.send(
                        {
                            "type": "notify",
                            "data": {"type": "delta_end", "reason": "user cancelled"},
                        }
                    )
        finally:
            conn.close()
            await conn.wait_closed()


def wait_frame(agent: FakeAgent, kind: str, timeout: float = 5) -> dict:
    """等待假 agent 收到指定类型的帧（跳过其它帧）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            msg = agent.frames.get(timeout=0.1)
        except queue.Empty:
            continue
        if msg.get("type") == kind:
            return msg
    raise AssertionError(f"no '{kind}' frame received")


def test_index_serves_chat_page():
    response = TestClient(web.app).get("/")
    assert response.status_code == 200
    assert "<title>Walle</title>" in response.text
    assert "/static/app/conversation.js" in response.text
    assert "/static/app/plugins.js" in response.text
    assert "工具审批" in response.text
    assert "停止" in response.text
    assert 'id="canvas-panel"' in response.text
    assert 'id="canvas-latest"' in response.text
    assert 'id="canvas-splitter"' in response.text
    assert "打开画布" in response.text
    assert "画布准备好了" in response.text
    assert 'id="workspace-button"' in response.text
    assert 'id="workspace-modal"' in response.text
    assert 'id="session-list"' in response.text
    assert 'id="agent-mode"' in response.text
    assert 'id="plugin-nav"' in response.text
    assert 'id="plugin-view"' in response.text
    assert "WalleConversation" in response.text
    assert "WallePlugins" in response.text
    assert "querySession" in response.text
    assert "queryPrompt" in response.text
    assert "session_taken_over" in response.text
    assert "本页已停止重连" in response.text
    assert "/api/workspaces" in response.text
    assert "/api/sessions" in response.text
    assert 'href="/courses"' not in response.text
    assert "tool-history" in response.text
    assert "tool_calls" in response.text
    assert "开始对话" in response.text
    assert "sin(x) 函数" in response.text
    assert "生成参数" in response.text
    assert "/static/marked.min.js" in response.text
    assert "/static/purify.min.js" in response.text
    assert "/static/katex/katex.min.css" in response.text
    assert "/static/katex/katex.min.js" in response.text
    assert "/static/katex/marked-katex.min.js" in response.text
    assert "mathMl" in response.text
    assert 'sandbox="allow-scripts' in response.text
    assert "allow-same-origin" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_static_markdown_assets_are_served():
    client = TestClient(web.app)
    marked = client.get("/static/marked.min.js")
    assert marked.status_code == 200
    assert "marked" in marked.text
    purify = client.get("/static/purify.min.js")
    assert purify.status_code == 200
    assert "DOMPurify" in purify.text
    for asset in (
        "/static/app/conversation.js",
        "/static/app/plugins.js",
        "/plugins/learning/views.js",
        "/plugins/learning/course-view.css",
    ):
        response = client.get(asset)
        assert response.status_code == 200, asset
    conversation = client.get("/static/app/conversation.js").text
    assert "重连中" in conversation
    assert 'type: "input.create"' in conversation
    assert 'type: "run.cancel"' in conversation
    assert "sendControl" in conversation
    assert "session.set_agent" in conversation
    assert "session.set_metadata" in conversation
    assert "targetSession !== state.sessionId" in conversation
    views = client.get("/plugins/learning/views.js").text
    assert "createCourseView" in views
    assert "createCoursesView" in views
    assert "WallePlugins.register" in views
    manifest = client.get("/api/web/plugins").json()["plugins"]
    assert manifest[0]["name"] == "learning"
    assert manifest[0]["nav"][0]["href"] == "/courses"
    katex = client.get("/static/katex/katex.min.js")
    assert katex.status_code == 200
    assert "katex" in katex.text.lower()
    katex_css = client.get("/static/katex/katex.min.css")
    assert katex_css.status_code == 200
    assert "KaTeX" in katex_css.text
    font = client.get("/static/katex/fonts/KaTeX_Main-Regular.woff2")
    assert font.status_code == 200
    assert font.content


def test_canvas_route_serves_session_file(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "CANVAS_ROOT", tmp_path)
    session = tmp_path / "web-abc123"
    session.mkdir()
    (session / "c1.html").write_text("<h1>canvas</h1>", encoding="utf-8")

    client = TestClient(web.app)
    response = client.get("/canvas/web-abc123/c1")
    assert response.status_code == 200
    assert response.text == "<h1>canvas</h1>"
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/canvas/web-abc123/missing").status_code == 404
    assert client.get("/canvas/web-abc123/bad.id").status_code == 404




def test_api_workspaces_and_sessions(monkeypatch, tmp_path):
    async def fake_workspaces(frame):
        assert frame == {"type": "list_workspaces"}
        return {
            "type": "workspaces",
            "current": str(tmp_path),
            "workspaces": [{"id": "w1", "path": str(tmp_path), "name": "work"}],
        }

    monkeypatch.setattr(web, "query_agent", fake_workspaces)
    client = TestClient(web.app)
    data = client.get("/api/workspaces").json()
    assert data["current"] == str(tmp_path)
    assert data["workspaces"][0]["name"] == "work"

    async def fake_sessions(frame):
        assert frame["type"] == "list_sessions"
        assert frame["cwd"] == str(tmp_path.resolve())
        return {
            "type": "sessions",
            "cwd": frame["cwd"],
            "sessions": [{"session_id": "s1", "title": "lesson"}],
        }

    monkeypatch.setattr(web, "query_agent", fake_sessions)
    data = client.get("/api/sessions", params={"cwd": str(tmp_path)}).json()
    assert data["cwd"] == str(tmp_path.resolve())
    assert data["sessions"] == [{"session_id": "s1", "title": "lesson"}]


def test_api_sessions_rejects_invalid_cwd(tmp_path):
    client = TestClient(web.app)
    assert client.get("/api/sessions").status_code == 400
    missing = tmp_path / "missing"
    response = client.get("/api/sessions", params={"cwd": str(missing)})
    assert response.status_code == 400


def test_web_ws_passes_workspace_and_session(monkeypatch, tmp_path):
    agent = FakeAgent()
    port = agent.start()
    monkeypatch.setattr(web, "PORT", port)
    try:
        url = (
            f"/ws/chat?cwd={quote(str(tmp_path))}"
            "&session=web-existing&attach=1&agent=tutor"
        )
        with TestClient(web.app).websocket_connect(url) as ws:
            assert ws.receive_json()["type"] == "welcome"
            assert agent.hello is not None
            assert agent.hello["session_id"] == "web-existing"
            assert agent.hello["cwd"] == str(tmp_path.resolve())
            assert agent.hello["attach"] is True
            assert agent.hello["agent"] == "tutor"
            assert agent.hello["call_timeout"] is None
            assert "course_id" not in agent.hello
            assert "lesson_id" not in agent.hello
    finally:
        agent.stop()


def test_web_ws_forwards_control_frames(monkeypatch, tmp_path):
    agent = FakeAgent()
    port = agent.start()
    monkeypatch.setattr(web, "PORT", port)
    try:
        url = (
            f"/ws/chat?cwd={quote(str(tmp_path))}"
            "&session=web-existing&attach=1&agent=learning"
        )
        with TestClient(web.app).websocket_connect(url) as ws:
            assert ws.receive_json()["type"] == "welcome"
            ws.send_json(
                {"type": "session.set_agent", "agent": "default"}
            )
            assert agent.frames.get(timeout=2) == {
                "type": "session.set_agent",
                "agent": "default",
            }
            ws.send_json(
                {
                    "type": "session.set_metadata",
                    "values": {"course_id": "course-a", "lesson_id": "lesson-a"},
                }
            )
            assert agent.frames.get(timeout=2) == {
                "type": "session.set_metadata",
                "values": {"course_id": "course-a", "lesson_id": "lesson-a"},
            }
    finally:
        agent.stop()


def test_web_ws_falls_back_to_create_when_session_missing(monkeypatch, tmp_path):
    agent = FakeAgent(attach_error=True)
    port = agent.start()
    monkeypatch.setattr(web, "PORT", port)
    try:
        url = (
            f"/ws/chat?cwd={quote(str(tmp_path))}"
            "&session=web-missing&attach=1"
        )
        with TestClient(web.app).websocket_connect(url) as ws:
            assert ws.receive_json()["type"] == "welcome"
            assert [item["attach"] for item in agent.hello_frames] == [True, False]
            assert all(
                item["session_id"] == "web-missing" for item in agent.hello_frames
            )
    finally:
        agent.stop()


def make_course_bundle(tmp_path):
    root = tmp_path / "courses"
    course = create_course(
        "测试课程",
        goal="测试目标",
        lessons=["第一课"],
        root=root,
    )
    lesson = write_lesson(
        course["id"],
        "第一课",
        "# 第一课\n\n{{canvas:demo}}\n",
        root=root,
    )
    canvas = root / course["id"] / "canvas" / "demo.html"
    canvas.parent.mkdir(parents=True, exist_ok=True)
    canvas.write_text(
        "<html><body><h1>演示画布</h1>demo</body></html>",
        encoding="utf-8",
    )
    return root, course, lesson


def test_course_pages_and_apis(monkeypatch, tmp_path):
    root, course, lesson = make_course_bundle(tmp_path)
    monkeypatch.setattr(learning_web, "COURSES_ROOT", root)
    client = TestClient(web.app)

    listing = client.get("/courses")
    assert listing.status_code == 200
    assert 'id="plugin-view"' in listing.text
    assert "/static/app/plugins.js" in listing.text

    page = client.get(f"/courses/{course['id']}")
    assert page.status_code == 200
    assert 'id="plugin-view"' in page.text
    views = client.get("/plugins/learning/views.js").text
    assert 'id="lesson-trigger"' in views
    assert 'id="lesson-menu"' in views
    assert 'id="course-splitter"' in views
    assert 'id="course-view"' in views
    assert 'id="course-title"' in views
    assert 'id="export-link"' in views
    assert 'id="panel-canvas"' in views
    assert 'id="panel-chat"' in views
    assert 'id="complete-lesson"' in views
    assert 'id="lesson-nav"' not in views
    assert 'id="qa-log"' not in views
    assert "WallePlugins.register" in views

    courses = client.get("/api/courses").json()["courses"]
    assert courses[0]["id"] == course["id"]
    assert courses[0]["lesson_count"] == 1

    manifest = client.get(f"/api/courses/{course['id']}").json()
    assert manifest["title"] == "测试课程"
    assert manifest["lessons"][0]["id"] == lesson["id"]
    assert manifest["lessons"][0]["written"] is True
    assert manifest["lessons"][0]["completed"] is False
    assert manifest["written_count"] == 1
    assert manifest["progress"]["completed_count"] == 0

    updated = client.post(
        f"/api/courses/{course['id']}/progress",
        json={"lesson_id": lesson["id"], "completed": True},
    )
    assert updated.status_code == 200
    assert updated.json()["progress"]["completed"] == [lesson["id"]]

    manifest = client.get(f"/api/courses/{course['id']}").json()
    assert manifest["lessons"][0]["completed"] is True
    listed = client.get("/api/courses").json()["courses"]
    assert listed[0]["completed_count"] == 1
    assert listed[0]["last_lesson"] == lesson["id"]
    assert listed[0]["resume_lesson"] == lesson["id"]

    invalid = client.post(
        f"/api/courses/{course['id']}/progress",
        json={"lesson_id": "missing", "completed": True},
    )
    assert invalid.status_code == 404

    content = client.get(
        f"/api/courses/{course['id']}/lessons/{lesson['id']}"
    ).json()
    assert content["lesson"]["markdown"].startswith("# 第一课")
    assert content["lesson"]["canvases"] == [
        {"id": "demo", "title": "演示画布"}
    ]

    rendered = client.get(f"/courses/{course['id']}/canvas/demo")
    assert rendered.status_code == 200
    assert rendered.text == "<html><body><h1>演示画布</h1>demo</body></html>"
    assert rendered.headers["cache-control"] == "no-store"

    exported = client.get(f"/courses/{course['id']}/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    assert "attachment" in exported.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(exported.content))
    assert "index.html" in archive.namelist()
    assert "data.js" in archive.namelist()

    assert client.get("/courses/missing").status_code == 404
    assert client.get(f"/courses/{course['id']}/canvas/missing").status_code == 404


def test_global_canvas_routes(monkeypatch, tmp_path):
    root, course, lesson = make_course_bundle(tmp_path)
    monkeypatch.setattr(learning_web, "COURSES_ROOT", root)
    global_root = tmp_path / "global-canvas"
    global_root.mkdir()
    (global_root / "shared.html").write_text(
        "<html><body>shared</body></html>", encoding="utf-8"
    )
    monkeypatch.setattr(web, "CANVAS_ROOT", global_root)
    monkeypatch.setattr(learning_web, "GLOBAL_CANVAS_ROOT", global_root)

    client = TestClient(web.app)
    generic = client.get("/canvas/any-session/shared")
    assert generic.status_code == 200
    assert generic.text == "<html><body>shared</body></html>"

    course_page = client.get(f"/courses/{course['id']}/canvas/shared")
    assert course_page.status_code == 200
    assert course_page.text == "<html><body>shared</body></html>"


def test_course_api_marks_unwritten_lessons(monkeypatch, tmp_path):
    root = tmp_path / "courses"
    course = create_course(
        "半成品课程",
        lessons=["已写", "未写"],
        root=root,
    )
    write_lesson(course["id"], "已写", "# 有内容", root=root)
    monkeypatch.setattr(learning_web, "COURSES_ROOT", root)

    data = TestClient(web.app).get(f"/api/courses/{course['id']}").json()
    assert [item["written"] for item in data["lessons"]] == [True, False]
    assert data["written_count"] == 1


def test_bridge_forwards_approval_reply_and_cancel(monkeypatch):
    """浏览器 reply / cancel 帧必须原样转到 agent 连接。"""
    agent = FakeAgent()
    port = agent.start()
    monkeypatch.setattr(web, "PORT", port)
    try:
        with TestClient(web.app).websocket_connect("/ws/chat") as ws:
            assert ws.receive_json()["type"] == "welcome"
            # Web 为人机审批通道：交互不设超时（避免用户离开后审批过期）
            assert agent.hello is not None
            assert agent.hello["call_timeout"] is None

            ws.send_json({"type": "input.create", "content": "run"})
            assert wait_frame(agent, "input.create")["content"] == "run"

            call = ws.receive_json()
            assert call["type"] == "call"
            assert call["data"]["type"] == "approval"

            ws.send_json(
                {"type": "reply", "id": call["id"], "data": {"approved": True}}
            )
            reply = wait_frame(agent, "reply")
            assert reply["id"] == call["id"]
            assert reply["data"] == {"approved": True}
            assert ws.receive_json()["data"]["type"] == "delta_end"

            ws.send_json({"type": "run.cancel"})
            wait_frame(agent, "run.cancel")
            tail = ws.receive_json()
            assert tail["data"] == {
                "type": "delta_end",
                "reason": "user cancelled",
            }
    finally:
        agent.stop()
