"""BYOK 会话级模型配置的回归测试。"""

from __future__ import annotations

import importlib
import time

import pytest
from fastapi.testclient import TestClient

web = importlib.import_module("walle.channel.web.app")
client = TestClient(web.app)

PUBLIC = {"api_key": "sk-x", "base_url": "https://8.8.8.8/v1", "model": "m1"}
PRIVATE = {"api_key": "sk-x", "base_url": "http://127.0.0.1:11434/v1", "model": "m1"}


@pytest.fixture(autouse=True)
def clean_pending(monkeypatch):
    web.PENDING_MODELS.clear()
    monkeypatch.delenv("WALLE_ALLOW_PRIVATE_UPSTREAM", raising=False)
    yield
    web.PENDING_MODELS.clear()


def test_register_public_upstream():
    resp = client.post("/api/session/s1/model", json=PUBLIC)
    assert resp.status_code == 200
    assert web.pending_model("s1")["model"] == "m1"


def test_reject_private_upstream():
    resp = client.post("/api/session/s1/model", json=PRIVATE)
    assert resp.status_code == 400


def test_allow_private_upstream(monkeypatch):
    monkeypatch.setenv("WALLE_ALLOW_PRIVATE_UPSTREAM", "1")
    resp = client.post("/api/session/s1/model", json=PRIVATE)
    assert resp.status_code == 200


def test_reject_bad_scheme():
    payload = {**PUBLIC, "base_url": "ftp://8.8.8.8/v1"}
    assert client.post("/api/session/s1/model", json=payload).status_code == 400


def test_reject_missing_field():
    payload = {"api_key": "k", "base_url": "https://8.8.8.8/v1"}
    assert client.post("/api/session/s1/model", json=payload).status_code == 400


def test_reject_bad_session_id():
    assert client.post("/api/session/bad%20id/model", json=PUBLIC).status_code == 400


def test_pending_model_expires():
    web.PENDING_MODELS["s9"] = (time.monotonic() - web.MODEL_TTL - 1, PUBLIC)
    assert web.pending_model("s9") is None


class FakeLink:
    """替代 agent 连接：回一帧 welcome，之后立即 EOF。"""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.sent = []

    async def recv(self):
        return self._payloads.pop(0) if self._payloads else None

    async def send(self, msg):
        self.sent.append(msg)

    def close(self):
        pass

    async def wait_closed(self):
        pass


def test_hello_carries_model_config(monkeypatch):
    captured = {}

    async def fake_open_link(payload):
        captured.update(payload)
        return FakeLink([{"type": "welcome"}])

    monkeypatch.setattr(web, "_open_link", fake_open_link)

    assert client.post("/api/session/s2/model", json=PUBLIC).status_code == 200
    with client.websocket_connect("/ws/chat?session=s2") as ws:
        assert ws.receive_json()["type"] == "welcome"

    assert captured["model"] == PUBLIC
    assert web.pending_model("s2") is None
