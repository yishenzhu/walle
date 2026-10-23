"""run.sh 控制台回归：语法、帮助与旧参数兼容。"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "scripts" / "run.sh"
SHELL_FILES = [RUN, *sorted((ROOT / "scripts" / "lib").glob("*.sh"))]

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="需要 bash")


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(RUN), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
    )


def test_shell_scripts_parse():
    for path in SHELL_FILES:
        result = subprocess.run(
            ["bash", "-n", str(path)],
            text=True,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"


def test_help_lists_core_commands():
    result = run_script("help")
    assert result.returncode == 0
    for command in ("up", "down", "status", "logs", "restart", "doctor", "test"):
        assert command in result.stdout
    assert "兼容旧参数" in result.stdout


def test_legacy_help_and_unknown_command():
    legacy = run_script("--help")
    assert legacy.returncode == 0
    assert "walle 控制台" in legacy.stdout

    unknown = run_script("not-a-command")
    assert unknown.returncode != 0
    assert "未知命令" in unknown.stderr
