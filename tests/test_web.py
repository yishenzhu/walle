"""Web 对话页最小回归：页面可加载。"""

from fastapi.testclient import TestClient

from ..channel.web.app import app


def test_index_serves_chat_page():
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert "<title>Walle</title>" in response.text
    assert "/ws/chat" in response.text
