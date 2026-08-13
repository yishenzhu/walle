"""Session 存储持久化 + attach/detach 生命周期测试。"""
import pytest

from ..core import Session, SessionRegistry, Runner, Agent, ToolExecutor
from ..conf import ToolConfig, ApprovalConfig, ApprovalDecision
from ..schemas import UserMessage
from ..messages import SQLiteMessages, InMemoryMessages

from .conftest import FakeChannel, FakeProvider


def make_session(session_id: str, db_path: str, transport=None, storage="sqlite"):
    """构造一个不依赖真实 LLM 的 Session（agent_factory 为最小 Agent）。"""
    runner = Runner(executor=ToolExecutor(ToolConfig(
        approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
    )))
    return Session(
        session_id=session_id,
        agent_factory=lambda: Agent(instruction="You are a helpful assistant."),
        runner=runner,
        transport=transport or FakeChannel(),
        storage=storage,
        db_path=db_path,
    )


@pytest.fixture
def provider():
    p = FakeProvider()
    FakeProvider.set_default(p)
    yield p
    FakeProvider.set_default(None)


class TestSessionStorage:
    async def test_default_storage_is_sqlite(self, tmp_path):
        """默认存储后端是 SQLite（跨连接持久化）。"""
        s = make_session("s1", str(tmp_path / "s.db"))
        assert isinstance(s._messages, SQLiteMessages)
        assert not isinstance(s._messages, InMemoryMessages)
        await s.close()

    async def test_memory_storage_when_configured(self, tmp_path):
        """显式配置 memory 时用内存存储。"""
        s = make_session("s1", str(tmp_path / "s.db"), storage="memory")
        assert isinstance(s._messages, InMemoryMessages)
        await s.close()

    async def test_history_persists_across_session_instances(self, tmp_path):
        """历史跨 Session 实例存活（模拟断开重连后恢复）。"""
        db = str(tmp_path / "s.db")

        s1 = make_session("reconnect", db)
        await s1._messages.add([UserMessage(content="hello")])
        await s1.close()

        # 重新连接：新 Session 实例，同一 db + session_id，历史仍在
        s2 = make_session("reconnect", db)
        msgs = await s2._messages.get()
        assert len(msgs) == 1
        assert msgs[0].content == "hello"
        await s2.close()

    async def test_session_id_isolation(self, tmp_path):
        """不同 session_id 历史互不串扰（同一 db 文件）。"""
        db = str(tmp_path / "s.db")

        s1 = make_session("a", db)
        s2 = make_session("b", db)
        await s1._messages.add([UserMessage(content="from-a")])
        await s2._messages.add([UserMessage(content="from-b")])

        r1 = await s1._messages.get()
        r2 = await s2._messages.get()
        assert len(r1) == 1 and r1[0].content == "from-a"
        assert len(r2) == 1 and r2[0].content == "from-b"
        await s1.close()
        await s2.close()


class TestSessionLifecycle:
    async def test_attach_detach(self, tmp_path):
        """attach 绑定 transport；detach 解除但不销毁 kernel/messages。"""
        s = make_session("life", str(tmp_path / "s.db"))
        assert s.attached is True

        s.detach()
        assert s.attached is False
        # detach 后状态仍在（kernel 未关、消息可读）
        assert s._kernel is not None
        await s._messages.add([UserMessage(content="after-detach")])
        msgs = await s._messages.get()
        assert len(msgs) == 1

        # 重连：attach 新 transport，状态还在
        s.attach(FakeChannel())
        assert s.attached is True
        msgs = await s._messages.get()
        assert msgs[0].content == "after-detach"
        await s.close()

    async def test_handle_requires_attach(self, tmp_path):
        """detached 会话调用 handle 报错（需先 attach）。"""
        from ..schemas import UserInput

        s = make_session("life2", str(tmp_path / "s.db"))
        s.detach()
        with pytest.raises(RuntimeError):
            await s.handle(UserInput(content="hi"))
        await s.close()


class TestSessionRegistry:
    async def test_register_get_remove(self, tmp_path):
        reg = SessionRegistry()
        s = make_session("reg-1", str(tmp_path / "s.db"))
        reg.register(s)
        assert reg.get("reg-1") is s
        assert reg.remove("reg-1") is s
        assert reg.get("reg-1") is None
        await s.close()

    async def test_duplicate_register_raises(self, tmp_path):
        reg = SessionRegistry()
        s = make_session("dup", str(tmp_path / "s.db"))
        reg.register(s)
        with pytest.raises(ValueError):
            reg.register(s)
        await s.close()

    async def test_list_shows_attach_state(self, tmp_path):
        reg = SessionRegistry()
        s = make_session("list-1", str(tmp_path / "s.db"))
        reg.register(s)
        s.detach()
        items = reg.list()
        assert len(items) == 1
        assert items[0]["session_id"] == "list-1"
        assert items[0]["attached"] is False
        assert items[0]["age_seconds"] >= 0
        await s.close()

    async def test_close_all(self, tmp_path):
        reg = SessionRegistry()
        s1 = make_session("c1", str(tmp_path / "s.db"))
        s2 = make_session("c2", str(tmp_path / "s.db"))
        reg.register(s1)
        reg.register(s2)
        await reg.close_all()
        assert reg.list() == []
