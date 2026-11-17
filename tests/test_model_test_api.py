"""「测试连接」接口的回归测试。"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

web = importlib.import_module("walle.channel.web.app")
client = TestClient(web.app)

CFG = {"api_key": "sk", "base_url": "https://8.8.8.8/v1", "model": "m"}


class FakeResponse:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text


class FakeClient:
    status = 200
    text = ""
    seen: dict = {}

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        FakeClient.seen = {"url": url, "json": json, "headers": headers}
        return FakeResponse(FakeClient.status, FakeClient.text)


@pytest.fixture(autouse=True)
def fake_http(monkeypatch):
    monkeypatch.setattr(web.httpx, "AsyncClient", FakeClient)
    monkeypatch.delenv("WALLE_ALLOW_PRIVATE_UPSTREAM", raising=False)
    FakeClient.status, FakeClient.text, FakeClient.seen = 200, "", {}


def test_ok():
    resp = client.post("/api/model/test", json=CFG)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert FakeClient.seen["url"] == "https://8.8.8.8/v1/chat/completions"
    assert FakeClient.seen["headers"]["Authorization"] == "Bearer sk"


def test_upstream_error_is_reported():
    FakeClient.status, FakeClient.text = 401, '{"error":"invalid key"}'
    resp = client.post("/api/model/test", json=CFG)
    assert resp.status_code == 502
    assert "401" in resp.json()["detail"]


def test_missing_field():
    assert client.post("/api/model/test", json={"api_key": "k"}).status_code == 400


def test_private_upstream_rejected():
    payload = {**CFG, "base_url": "http://127.0.0.1:11434/v1"}
    assert client.post("/api/model/test", json=payload).status_code == 400


def test_custom_headers_forwarded():
    payload = {**CFG, "headers": {"x-opencode-session": "abc-123"}}
    assert client.post("/api/model/test", json=payload).status_code == 200
    assert FakeClient.seen["headers"]["x-opencode-session"] == "abc-123"


def test_blocked_header_rejected():
    payload = {**CFG, "headers": {"Authorization": "Bearer other"}}
    assert client.post("/api/model/test", json=payload).status_code == 400


def test_header_with_newline_rejected():
    payload = {**CFG, "headers": {"x-test": "a\nb"}}
    assert client.post("/api/model/test", json=payload).status_code == 400
