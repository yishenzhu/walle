"""运行期路径与配置加载的回归测试。"""

from __future__ import annotations

import pytest

from ..conf import Config
from ..conf import config as config_mod

CONF = """
log:
  level: "INFO"
  path: "logs/agent.log"
  backup_count: 3
"""


def test_resolve_path_uses_home(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "HOME", tmp_path)
    assert config_mod.resolve_path("logs/a.log") == str((tmp_path / "logs/a.log").resolve())


def test_resolve_path_keeps_absolute(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "HOME", tmp_path)
    absolute = tmp_path / "x" / "y.log"
    assert config_mod.resolve_path(str(absolute)) == str(absolute.resolve())


def test_load_defaults_to_home_conf(tmp_path, monkeypatch):
    conf = tmp_path / "conf.yaml"
    conf.write_text(CONF, encoding="utf-8")
    monkeypatch.delenv("WALLE_CONF", raising=False)
    monkeypatch.setattr(config_mod, "CONF_PATH", conf)

    assert Config.load().log.level == "INFO"


def test_load_missing_conf_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("WALLE_CONF", raising=False)
    monkeypatch.setattr(config_mod, "CONF_PATH", tmp_path / "missing.yaml")

    with pytest.raises(RuntimeError, match="init-home"):
        Config.load()


def test_load_absolute_walle_conf(tmp_path, monkeypatch):
    conf = tmp_path / "prod.yaml"
    conf.write_text(CONF, encoding="utf-8")
    monkeypatch.setenv("WALLE_CONF", str(conf))

    assert Config.load().log.path == "logs/agent.log"


def test_load_relative_walle_conf_uses_source_root(monkeypatch):
    monkeypatch.setenv("WALLE_CONF", "conf.prod.yaml")
    assert Config.load().log.level == "INFO"
