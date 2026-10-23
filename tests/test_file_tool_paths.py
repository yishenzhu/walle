"""read / write / edit 的路径解析：相对路径落到会话 cwd。"""

from contextlib import contextmanager
from pathlib import Path

from ..infra import tool_context
from ..tools.builtin.edit import edit
from ..tools.builtin.read import read
from ..tools.builtin.write import write
from .conftest import make_session


@contextmanager
def session_cwd(path):
    token = tool_context.set(make_session(cwd=str(path)))
    try:
        yield
    finally:
        tool_context.reset(token)


async def test_read_resolves_relative_to_session_cwd(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("hello", encoding="utf-8")
    with session_cwd(workspace):
        assert await read("note.md") == "hello"


async def test_write_resolves_relative_to_session_cwd(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    relative = f"walle-test-{tmp_path.name}/new.txt"
    with session_cwd(workspace):
        out = await write(relative, "content")
    assert "created" in out
    assert (workspace / relative).read_text(encoding="utf-8") == "content"
    assert not (Path.cwd() / relative).exists()


async def test_edit_resolves_relative_to_session_cwd(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.md").write_text("old", encoding="utf-8")
    with session_cwd(workspace):
        out = await edit("note.md", "old", "new")
    assert "edited" in out
    assert (workspace / "note.md").read_text(encoding="utf-8") == "new"


async def test_absolute_path_ignores_session_cwd(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("absolute", encoding="utf-8")
    with session_cwd(workspace):
        assert await read(str(outside)) == "absolute"


async def test_relative_path_falls_back_to_process_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "local.txt").write_text("local", encoding="utf-8")
    assert await read("local.txt") == "local"
