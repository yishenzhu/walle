"""会话中途切换模型（session.set_model）的回归测试。"""

from __future__ import annotations

import pytest

from ..channel.frames import Frame
from ..channel.server import SessionServer

CFG = {"api_key": "k", "base_url": "https://api.example.com/v1", "model": "m2"}


class FakeSessions:
    def provider_for(self, cfg):
        return ("provider", cfg.model if cfg is not None else None)


class FakeSession:
    def __init__(self):
        self.transport = object()
        self.provider = ("default", None)

    def set_provider(self, provider):
        self.provider = provider


def server() -> SessionServer:
    return SessionServer(FakeSessions())


async def test_set_model_swaps_provider_without_reconnect():
    session = FakeSession()
    await server()._on_inbound(
        session,
        session.transport,
        "s1",
        {"type": Frame.SESSION_SET_MODEL, "model": CFG},
    )
    assert session.provider == ("provider", "m2")


async def test_set_model_null_restores_default():
    session = FakeSession()
    await server()._on_inbound(
        session, session.transport, "s1", {"type": Frame.SESSION_SET_MODEL, "model": None}
    )
    assert session.provider == ("provider", None)


async def test_set_model_rejects_invalid_config():
    session = FakeSession()
    with pytest.raises(ValueError, match="valid model config"):
        await server()._on_inbound(
            session,
            session.transport,
            "s1",
            {"type": Frame.SESSION_SET_MODEL, "model": {"api_key": "k"}},
        )
