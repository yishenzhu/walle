"""welcome 帧的会话状态字段。"""

from __future__ import annotations

from ..channel.server import SessionServer


class FakeSessions:
    def provider_for(self, cfg):
        return None


class FakeMessage:
    def model_dump(self, mode="json"):
        return {"role": "user", "content": "hi"}


class FakeSession:
    def __init__(self, state: str):
        self.id = "s1"
        self.state = state

    async def conversation_history(self):
        return [FakeMessage()]


async def test_welcome_reports_running():
    frame = await SessionServer(FakeSessions())._welcome_frame(FakeSession("running"))
    assert frame["running"] is True
    assert frame["history"][0]["role"] == "user"


async def test_welcome_reports_idle():
    frame = await SessionServer(FakeSessions())._welcome_frame(FakeSession("idle"))
    assert frame["running"] is False
