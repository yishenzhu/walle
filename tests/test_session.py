"""Session 存储持久化测试（默认 SQLite：历史跨连接/重启保留）。"""
import pytest

from ..core import Session, Runner, Agent, ToolExecutor
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
        transport=transport or FakeChannel(),
        agent_factory=lambda: Agent(instruction="You are a helpful assistant."),
        runner=runner,
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
