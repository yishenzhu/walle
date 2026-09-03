"""Session 存储持久化 + attach/detach 生命周期测试。"""

import pytest

from ..core import Agent, Event, Session, SessionRegistry
from ..conf import ToolConfig, ApprovalConfig, ApprovalDecision
from ..schemas import UserMessage
from ..messages import SQLiteMessages, InMemoryMessages

from .conftest import FakeChannel, FakeProvider


def make_session(session_id: str, db_path: str, transport=None, storage="sqlite"):
    """构造一个不依赖真实 LLM 的 Session（agent_factory 为最小 Agent）。"""
    return Session(
        session_id=session_id,
        agent_factory=lambda _name=None: Agent(
            instruction="You are a helpful assistant."
        ),
        tool_config=ToolConfig(
            approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
        ),
        transport=transport or FakeChannel(),
        storage=storage,
        db_path=db_path,
    )


def make_registry(db_path: str) -> SessionRegistry:
    """构造带 Session 构造参数的 registry（register 测试用）。"""
    return SessionRegistry(
        agent_factory=lambda _name=None: Agent(
            instruction="You are a helpful assistant."
        ),
        tool_config=ToolConfig(
            approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
        ),
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
        """attach 绑定 transport；detach 解除但不销毁 messages。"""
        s = make_session("life", str(tmp_path / "s.db"))
        assert s.attached is True

        s.detach()
        assert s.attached is False
        # detach 后状态仍在（消息可读）
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
        reg = make_registry(str(tmp_path / "s.db"))
        s = make_session("reg-1", str(tmp_path / "s.db"))
        reg.register(s)
        assert reg.get("reg-1") is s
        assert reg.remove("reg-1") is s
        assert reg.get("reg-1") is None
        await s.close()

    async def test_duplicate_register_raises(self, tmp_path):
        reg = make_registry(str(tmp_path / "s.db"))
        s = make_session("dup", str(tmp_path / "s.db"))
        reg.register(s)
        with pytest.raises(ValueError):
            reg.register(s)
        await s.close()

    async def test_list_shows_attach_state(self, tmp_path):
        reg = make_registry(str(tmp_path / "s.db"))
        s = make_session("list-1", str(tmp_path / "s.db"))
        reg.register(s)
        s.detach()
        items = reg.list()
        assert len(items) == 1
        assert items[0]["session_id"] == "list-1"
        assert items[0]["attached"] is False
        assert items[0]["age_seconds"] >= 0
        await s.close()

    async def test_close(self, tmp_path):
        reg = make_registry(str(tmp_path / "s.db"))
        s1 = make_session("c1", str(tmp_path / "s.db"))
        s2 = make_session("c2", str(tmp_path / "s.db"))
        reg.register(s1)
        reg.register(s2)
        await reg.close()
        assert reg.list() == []


class TestSessionCommandDispatch:
    async def test_command_reply_bypasses_runner(self, tmp_path):
        """斜杠命令命中：回复经 channel 推送，不经 agent/runner。"""
        from ..schemas import UserInput
        from ..core import ExtensionAPI, ExtensionRegistry, Extension

        ch = FakeChannel()
        # 构造一个注册了 /ping 命令的扩展声明，激活进会话
        loader = ExtensionRegistry()

        async def load_cli(api: ExtensionAPI):
            async def ping(args: str) -> str:
                return "pong"

            api.register_command("ping", "ping", ping)

        loader.add("cli", load_cli)
        await loader.load()
        ext = loader.extensions[0]

        s = Session(
            session_id="cmd-1",
            agent_factory=lambda _name=None: Agent(
                instruction="You are a helpful assistant."
            ),
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
            ),
            extensions=[ext],
            transport=ch,
            storage="memory",
            db_path=str(tmp_path / "s.db"),
        )

        # 命中命令：无 provider 也正常（不经 runner），回复被推送
        await s.handle(UserInput(content="/ping"))
        types = [type(e).__name__ for e in ch.events]
        assert types == ["Delta", "DeltaEnd"]  # 只有回复推送，无 agent 输出
        assert ch.events[0].delta == "pong"

        await s.close()


class TestSessionIsolation:
    """会话隔离：不同会话可选激活不同扩展，工具/事件互不干扰。"""

    async def _registry(self, tmp_path) -> SessionRegistry:
        from ..core import ExtensionAPI, ExtensionRegistry
        from ..tools import Tool

        loader = ExtensionRegistry()

        async def ext_a(api: ExtensionAPI):
            async def fa(args):
                return "a"

            api.register_tool(
                Tool(name="tool_a", description="a", parameters={}, fn=fa)
            )
            api.on(Event.AGENT_START, lambda **kw: None)

        async def ext_b(api: ExtensionAPI):
            async def fb(args):
                return "b"

            api.register_tool(
                Tool(name="tool_b", description="b", parameters={}, fn=fb)
            )

        loader.add("ext_a", ext_a)
        loader.add("ext_b", ext_b)
        await loader.load()
        pool = [e for e in loader.extensions if e.error is None]

        reg = SessionRegistry(
            agent_factory=lambda _name=None: Agent(
                instruction="You are a helpful assistant."
            ),
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
            ),
            extensions=pool,
            storage="memory",
            db_path=str(tmp_path / "s.db"),
        )
        return reg

    async def test_sessions_activate_different_extensions(self, tmp_path):
        """会话 1 用全部扩展；会话 2 只激活 ext_a——工具表不同、事件隔离。"""
        reg = await self._registry(tmp_path)

        class Conn:
            def __init__(self, chat_id):
                self.chat_id = chat_id

        s1 = reg.create(Conn("s1"))  # 默认：全部扩展
        s2 = reg.create(Conn("s2"), ext_names=["ext_a"])  # 只激活 ext_a

        names1 = {t.name for t in s1.tools.all_tools()}
        names2 = {t.name for t in s2.tools.all_tools()}
        assert {"tool_a", "tool_b"} <= names1  # 会话 1 有全部
        assert names2 == {"tool_a"}  # 会话 2 只有 ext_a

        # 事件隔离：各自的 bus 独立
        assert s1.ext_runner.active_names == {"ext_a", "ext_b"}
        assert s2.ext_runner.active_names == {"ext_a"}
        # 审批策略随会话独立（各自 executor 实例）
        assert s1.tool_executor is not s2.tool_executor
        assert s1.agent_runner is not s2.agent_runner

        await reg.close()

    async def test_shared_mcp_tools_visible_in_session(self, tmp_path):
        """进程级共享 MCP 客户端：其远端工具出现在会话工具表（MCP 不每会话重连）。"""
        from ..tools import Tool
        from ..tools.mcp import MCP

        async def fake_fn(args):
            return "mcp-result"

        fake_tool = Tool(
            name="mcp_remote_search",
            description="remote",
            parameters={"type": "object"},
            fn=fake_fn,
        )

        class FakeMcpClient:
            name = "remote"
            tools = [fake_tool]

        shared_mcp = MCP()
        shared_mcp._clients.append(FakeMcpClient())

        reg = SessionRegistry(
            agent_factory=lambda _name=None: Agent(
                instruction="You are a helpful assistant."
            ),
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
            ),
            mcp=shared_mcp,
            storage="memory",
            db_path=str(tmp_path / "s.db"),
        )

        class Conn:
            chat_id = "mcp-1"

        s = reg.create(Conn())
        names = {t.name for t in s.tools.all_tools()}
        assert "mcp_remote_search" in names  # MCP 工具经共享容器进会话

        await reg.close()
