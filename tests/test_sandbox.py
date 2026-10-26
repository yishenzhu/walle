"""沙箱（bubblewrap）测试。"""
import asyncio
import shutil
import subprocess

import pytest

from ..conf import PROJECT_ROOT
from ..tools import Sandbox, SandboxConfig

HAVE_BWRAP = shutil.which("bwrap") is not None

pytestmark = pytest.mark.skipif(not HAVE_BWRAP, reason="bwrap not installed")

TEST_ROOT = PROJECT_ROOT / "data" / "sandbox" / "_test"


@pytest.fixture
def ws():
    """宿主真实存在的可写工作区（会话 cwd 角色）。"""
    d = TEST_ROOT / "ws"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(TEST_ROOT, ignore_errors=True)


def make_sandbox(**kw) -> Sandbox:
    defaults = dict(hidden_paths=[], network=True)
    defaults.update(kw)
    return Sandbox(SandboxConfig(**defaults))


async def exec_argv(argv: list[str]) -> str:
    """执行完整 argv（如 Sandbox.argv 产出），返回 stdout+stderr。"""
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    return out.decode("utf-8", errors="replace")


# ── argv 构造 ────────────────────────────────────────

class TestSandbox:
    def test_ro_bind_root_present(self):
        args = make_sandbox().argv("/work", "x")
        assert args[:3] == ["bwrap", "--ro-bind", "/"]

    def test_cwd_bound_writable(self):
        args = make_sandbox().argv("/home/u/proj", "x")
        assert "--bind" in args
        i = args.index("--bind")
        assert args[i : i + 3] == ["--bind", "/home/u/proj", "/home/u/proj"]

    def test_hidden_dir_masked_with_tmpfs(self, ws):
        secret = ws / "secret_dir"
        secret.mkdir()
        args = make_sandbox(hidden_paths=[str(secret)]).argv(str(ws), "x")
        assert "--tmpfs" in args
        assert str(secret) in args

    def test_hidden_file_masked_with_devnull(self, ws):
        secret = ws / "secret.txt"
        secret.touch()
        args = make_sandbox(hidden_paths=[str(secret)]).argv(str(ws), "x")
        pairs = [
            (args[i + 1], args[i + 2]) for i, a in enumerate(args) if a == "--bind"
        ]
        assert ("/dev/null", str(secret)) in pairs

    def test_hidden_missing_path_skipped(self):
        args = make_sandbox(hidden_paths=["/nonexistent/x"]).argv("/work", "x")
        assert "/nonexistent/x" not in args

    def test_network_disabled_adds_unshare(self):
        assert "--unshare-net" in make_sandbox(network=False).argv("/work", "x")

    def test_network_enabled_no_unshare(self):
        assert "--unshare-net" not in make_sandbox(network=True).argv("/work", "x")

    def test_default_hidden_includes_ssh(self):
        assert any("ssh" in h for h in SandboxConfig().hidden_paths)

    def test_argv_ends_with_bash_c(self):
        argv = make_sandbox().argv("/work", "ls -la")
        assert argv[-3:] == ["bash", "-c", "ls -la"]


# ── 沙箱插件：同名覆盖内置 bash ──────────────────────

class TestSandboxExt:
    def test_registers_tool_named_bash(self, monkeypatch):
        monkeypatch.setattr(
            "walle.tools.sandbox.shutil.which", lambda _: "/usr/bin/bwrap"
        )
        from ..infra import SessionToolTable
        from ..plugin import PluginHost
        from ..spec import ToolService

        async def run():
            host = PluginHost()
            table = SessionToolTable()
            await host.root.register(ToolService, table)
            try:
                entries = await host.launch([Sandbox().plugin()])
                assert entries[0].state.value == "active"
                assert table.has("bash")
            finally:
                await host.dispose()

        asyncio.run(run())

    def test_missing_bwrap_marks_plugin_failed(self, monkeypatch):
        monkeypatch.setattr("walle.tools.sandbox.shutil.which", lambda _: None)
        from ..infra import SessionToolTable
        from ..plugin import PluginHost
        from ..spec import ToolService

        async def run():
            host = PluginHost()
            await host.root.register(ToolService, SessionToolTable())
            entries = await host.launch([Sandbox().plugin()])
            try:
                assert entries[0].state.value == "failed"
                assert "bwrap" in (entries[0].error or "")
            finally:
                await host.dispose()

        asyncio.run(run())


# ── 集成（真跑 bwrap） ───────────────────────────────

class TestIntegration:
    async def test_workspace_writable(self, ws):
        argv = make_sandbox().argv(str(ws), f"touch {ws}/probe && echo ok")
        out = await exec_argv(argv)
        assert "ok" in out
        assert (ws / "probe").exists()

    async def test_outside_write_fails(self, ws):
        outside = TEST_ROOT / "outside_probe"
        outside.write_text("x")
        argv = make_sandbox().argv(str(ws), f"echo hi > {outside} 2>&1; echo rc=$?")
        out = await exec_argv(argv)
        assert "rc=" in out
        assert outside.read_text() == "x"

    async def test_root_readonly_blocks_rm_rf(self, ws):
        argv = make_sandbox().argv(
            str(ws),
            "rm -rf --no-preserve-root / 2>&1 | head -1; "
            "ls /etc/hostname >/dev/null 2>&1 && echo etc-ok",
        )
        out = await exec_argv(argv)
        assert "Read-only" in out
        assert "etc-ok" in out

    async def test_hidden_file_invisible(self, ws):
        secret = ws / "secret.txt"
        secret.write_text("top-secret")
        argv = make_sandbox(hidden_paths=[str(secret)]).argv(
            str(ws), f"cat {secret} 2>&1; echo done"
        )
        out = await exec_argv(argv)
        assert "top-secret" not in out
        assert "done" in out

    async def test_hidden_dir_invisible(self, ws):
        secret_dir = ws / "secret_dir"
        secret_dir.mkdir()
        (secret_dir / "key.txt").write_text("private-key")
        argv = make_sandbox(hidden_paths=[str(secret_dir)]).argv(
            str(ws), f"ls -A {secret_dir} 2>&1 | wc -l"
        )
        out = await exec_argv(argv)
        assert out.strip() == "0"
