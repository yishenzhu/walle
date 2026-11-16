"""访问日志配置的回归测试。"""

from __future__ import annotations

import pytest

from ..channel.web import logconf


def test_access_log_disabled_by_default(monkeypatch):
    monkeypatch.delenv("WALLE_ACCESS_LOG", raising=False)

    assert logconf.access_log_config() is None


def test_access_log_writes_to_home(tmp_path, monkeypatch):
    monkeypatch.setattr(logconf, "LOGS", tmp_path)
    monkeypatch.setenv("WALLE_ACCESS_LOG", "1")

    config = logconf.access_log_config()

    assert config is not None
    handler = config["handlers"]["access_file"]
    assert handler["filename"] == str(tmp_path / "access.log")
    assert "access_file" in config["loggers"]["uvicorn.access"]["handlers"]


@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_access_log_disabled_values(monkeypatch, value):
    monkeypatch.setenv("WALLE_ACCESS_LOG", value)

    assert logconf.access_log_config() is None
