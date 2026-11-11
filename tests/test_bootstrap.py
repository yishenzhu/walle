"""默认内容初始化的回归测试：直接跑 deploy/init-home.sh。"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from ..conf import config as config_mod

INIT_SCRIPT = config_mod.SOURCE_ROOT / "deploy" / "init-home.sh"
TEMPLATES = config_mod.SOURCE_ROOT / "templates"


def run_init(home, templates) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "WALLE_HOME": str(home), "WALLE_TEMPLATES": str(templates)}
    return subprocess.run(
        ["sh", str(INIT_SCRIPT)], env=env, capture_output=True, text=True, check=True
    )


@pytest.fixture
def workspace(tmp_path):
    """隔离的模板副本与 WALLE_HOME。"""
    templates = tmp_path / "templates"
    shutil.copytree(TEMPLATES, templates)
    return tmp_path / "home", templates


def test_init_home_seeds_templates(workspace):
    home, templates = workspace
    run_init(home, templates)

    assert (home / ".agent" / "agents" / "default.md").is_file()
    assert (home / ".agent" / "skills" / "grilling" / "SKILL.md").is_file()
    assert (home / "conf.yaml").is_file()
    assert (home / "data" / "canvas").is_dir()
    assert (home / "logs").is_dir()
    assert (home / ".agent" / ".templates").is_file()


def test_init_home_keeps_user_files(workspace):
    home, templates = workspace
    agent = home / ".agent"
    (agent / "agents").mkdir(parents=True)
    (agent / "agents" / "default.md").write_text("custom", encoding="utf-8")
    home.mkdir(parents=True, exist_ok=True)
    (home / "conf.yaml").write_text("custom", encoding="utf-8")

    run_init(home, templates)

    assert (agent / "agents" / "default.md").read_text(encoding="utf-8") == "custom"
    assert (home / "conf.yaml").read_text(encoding="utf-8") == "custom"


def test_init_home_does_not_resurrect_deleted_file(workspace):
    home, templates = workspace
    run_init(home, templates)
    deleted = home / ".agent" / "agents" / "tutor.md"
    deleted.unlink()

    run_init(home, templates)

    assert not deleted.exists()


def test_init_home_picks_up_new_template(workspace):
    home, templates = workspace
    run_init(home, templates)

    skill = templates / "agent" / "skills" / "new-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: new-skill\n---\n", encoding="utf-8")

    run_init(home, templates)

    assert (home / ".agent" / "skills" / "new-skill" / "SKILL.md").is_file()
