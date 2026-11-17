"""会话中途切换模型（session.set_model）的回归测试。"""

from __future__ import annotations

import pytest

from ..channel.frames import Frame
from ..channel.server import SessionServer

CFG = {"api_key": "k", "base_url": "https://8.8.8.8/v1", "model": "m2"}


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


class TestUpstreamValidation:
    async def test_rejects_private(self, monkeypatch):
        from ..infra.upstream import validate_upstream

        monkeypatch.delenv("WALLE_ALLOW_PRIVATE_UPSTREAM", raising=False)
        with pytest.raises(ValueError, match="内网"):
            await validate_upstream("http://127.0.0.1:11434/v1")

    async def test_rejects_bad_scheme(self):
        from ..infra.upstream import validate_upstream

        with pytest.raises(ValueError, match="http"):
            await validate_upstream("ftp://8.8.8.8/v1")

    async def test_allows_public(self, monkeypatch):
        from ..infra.upstream import validate_upstream

        monkeypatch.delenv("WALLE_ALLOW_PRIVATE_UPSTREAM", raising=False)
        await validate_upstream("https://8.8.8.8/v1")

    async def test_allow_private_env(self, monkeypatch):
        from ..infra.upstream import validate_upstream

        monkeypatch.setenv("WALLE_ALLOW_PRIVATE_UPSTREAM", "1")
        await validate_upstream("http://127.0.0.1:11434/v1")

    async def test_unresolved_is_not_reported_as_private(self, monkeypatch):
        from ..infra.upstream import validate_upstream

        monkeypatch.delenv("WALLE_ALLOW_PRIVATE_UPSTREAM", raising=False)
        with pytest.raises(ValueError, match="解析失败"):
            await validate_upstream("https://no-such-host.invalid/v1")
