"""FileMessages / FileProjectionStore 测试（集中存储、按工作目录分组）。"""

import asyncio
import json
from pathlib import Path

import pytest

from ..messages import (
    FileMessages,
    FileProjectionStore,
    ProjectedMessages,
    build_history,
    list_sessions,
    list_workspaces,
)
from ..messages.file import (
    MESSAGES_FILE,
    META_FILE,
    PROJECTION_FILE,
    WORKSPACE_FILE,
    canonical_cwd,
    session_dir_name,
    workspace_dir,
    workspace_id,
)
from ..spec import UserMessage


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "project"
    path.mkdir()
    return path


@pytest.fixture
def workspace(tmp_path, project):
    return workspace_dir(project, tmp_path / "sessions")


@pytest.fixture
def messages(workspace, project):
    return FileMessages(workspace / session_dir_name("test"), "test", cwd=project)


class TestFileMessages:
    async def test_add_and_get(self, messages):
        await messages.add(
            [UserMessage(content="hello"), UserMessage(content="world")]
        )
        result = await messages.get()
        assert [m.content for m in result] == ["hello", "world"]

    async def test_query_window(self, messages):
        for i in range(5):
            await messages.add([UserMessage(content=f"msg{i}")])
        result = await messages.query(offset=1, limit=2)
        assert [m.content for m in result] == ["msg1", "msg2"]

    async def test_search_returns_reverse_order(self, messages):
        await messages.add(
            [
                UserMessage(content="Alpha beta"),
                UserMessage(content="gamma"),
                UserMessage(content="alpha gamma"),
            ]
        )
        result = await messages.search("alpha")
        assert [(i, m.content) for i, m in result] == [
            (2, "alpha gamma"),
            (0, "Alpha beta"),
        ]
        result = await messages.search("alpha gamma")
        assert [(i, m.content) for i, m in result] == [(2, "alpha gamma")]

    async def test_pop(self, messages):
        await messages.add(
            [UserMessage(content="first"), UserMessage(content="second")]
        )
        popped = await messages.pop()
        assert popped is not None
        assert popped.content == "first"
        assert [m.content for m in await messages.get()] == ["second"]

    async def test_pop_empty(self, messages):
        assert await messages.pop() is None

    async def test_clear(self, messages):
        await messages.add([UserMessage(content="hello")])
        await messages.clear()
        assert await messages.get() == []

    async def test_reopen_reads_persisted_messages(self, workspace):
        directory = workspace / session_dir_name("reopen")
        first = FileMessages(directory, "reopen")
        await first.add([UserMessage(content="persisted")])
        await first.close()

        second = FileMessages(directory, "reopen")
        result = await second.get()
        assert [m.content for m in result] == ["persisted"]
        await second.close()

    async def test_corrupt_tail_is_skipped_and_healed(self, workspace):
        directory = workspace / session_dir_name("corrupt")
        first = FileMessages(directory, "corrupt")
        await first.add([UserMessage(content="ok")])
        await first.close()
        with (directory / MESSAGES_FILE).open("a", encoding="utf-8") as f:
            f.write('{"role":"user","content":"broken')

        second = FileMessages(directory, "corrupt")
        result = await second.get()
        assert [m.content for m in result] == ["ok"]
        raw = (directory / MESSAGES_FILE).read_text(encoding="utf-8")
        assert "broken" not in raw
        assert raw.endswith("\n")
        await second.close()

    async def test_session_directories_are_isolated(self, workspace):
        s1 = FileMessages(workspace / session_dir_name("s1"), "s1")
        s2 = FileMessages(workspace / session_dir_name("s2"), "s2")
        await s1.add([UserMessage(content="from-s1")])
        await s2.add([UserMessage(content="from-s2")])
        assert [m.content for m in await s1.get()] == ["from-s1"]
        assert [m.content for m in await s2.get()] == ["from-s2"]
        await s1.close()
        await s2.close()

    async def test_meta_and_workspace_files(self, project, workspace, messages):
        await messages.add([UserMessage(content="hello")])
        meta = json.loads((messages.path / META_FILE).read_text(encoding="utf-8"))
        assert meta["session_id"] == "test"
        assert meta["workspace_id"] == messages.path.parent.name
        assert meta["cwd"] == str(canonical_cwd(project))
        info = json.loads((workspace / WORKSPACE_FILE).read_text(encoding="utf-8"))
        assert info["id"] == workspace_id(project)
        assert info["path"] == str(canonical_cwd(project))
        await messages.close()


class TestFileProjectionStore:
    async def test_roundtrip_and_clear(self, messages):
        store = FileProjectionStore(messages.path / PROJECTION_FILE)
        assert await store.load() is None
        await store.save(3)
        assert await store.load() == 3
        await store.clear()
        assert await store.load() is None

    async def test_projection_survives_reopen(self, workspace):
        directory = workspace / session_dir_name("projection")
        first = ProjectedMessages(
            FileMessages(directory, "projection"),
            projection_store=FileProjectionStore(directory / PROJECTION_FILE),
        )
        await first.add(
            [UserMessage(content="u1"), UserMessage(content="u2")]
        )
        await first.set_projection(1)
        await first.close()

        second = ProjectedMessages(
            FileMessages(directory, "projection"),
            projection_store=FileProjectionStore(directory / PROJECTION_FILE),
        )
        result = await second.get()
        assert [m.content for m in result] == ["u2"]
        await second.close()


class TestSessionListing:
    async def test_lists_workspaces_and_sessions(self, tmp_path, project):
        root = tmp_path / "sessions"
        other = tmp_path / "other"
        other.mkdir()
        first = workspace_dir(project, root)
        second = workspace_dir(other, root)

        s1 = FileMessages(first / session_dir_name("s1"), "s1", cwd=project)
        await s1.add([UserMessage(content="Learn Fourier transforms")])
        await asyncio.sleep(0.01)
        s2 = FileMessages(first / session_dir_name("s2"), "s2", cwd=project)
        await s2.add([UserMessage(content="Linear algebra")])
        s3 = FileMessages(second / session_dir_name("s3"), "s3", cwd=other)
        await s3.add([UserMessage(content="Other workspace")])
        await asyncio.gather(s1.close(), s2.close(), s3.close())

        workspaces = list_workspaces(root)
        assert {item["path"] for item in workspaces} == {
            str(canonical_cwd(project)),
            str(canonical_cwd(other)),
        }
        counts = {item["path"]: item["session_count"] for item in workspaces}
        assert counts[str(canonical_cwd(project))] == 2
        assert counts[str(canonical_cwd(other))] == 1

        items = list_sessions(project, root)
        assert [item["session_id"] for item in items] == ["s2", "s1"]
        assert [item["title"] for item in items] == [
            "Linear algebra",
            "Learn Fourier transforms",
        ]
        assert all(item["cwd"] == str(canonical_cwd(project)) for item in items)

    async def test_missing_directory_has_no_sessions(self, tmp_path, project):
        assert list_sessions(tmp_path / "missing", tmp_path / "sessions") == []
        assert list_workspaces(tmp_path / "sessions") == []


class TestBuildHistory:
    async def test_file_backend_lives_outside_workspace(self, tmp_path):
        project = tmp_path / "project"
        sessions = tmp_path / "sessions"
        history = build_history("file", sessions, "s1", cwd=project)
        assert isinstance(history, ProjectedMessages)
        assert isinstance(history.underlying, FileMessages)
        assert Path(history.underlying.path).is_relative_to(sessions)
        assert not Path(history.underlying.path).is_relative_to(project)
        await history.close()

    async def test_unsafe_session_id_cannot_escape_workspace(self, tmp_path):
        workspace = workspace_dir(tmp_path / "project", tmp_path / "sessions")
        name = session_dir_name("../../etc/passwd")
        directory = workspace / name
        assert "/" not in name
        assert ".." not in name
        assert directory.is_relative_to(workspace)
