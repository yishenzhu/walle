"""Session 存储持久化 + attach/detach 生命周期测试。"""

import pytest

from ..conf import ApprovalConfig, ApprovalDecision, ToolConfig
from ..core import Session, SessionRegistry
from ..infra import AgentStartEvent, MessageDeltaEvent, MessageEndEvent
from ..messages import (
    InMemoryMessages,
    ProjectedMessages,
    SQLiteMessages,
    build_history,
)
from ..spec import ModelConfig, UserMessage
from .conftest import FakeChannel, FakeProvider


def make_session(session_id: str, db_path: str, transport=None, storage="sqlite"):
    """构造一个 Session（agent 由内部按名加载 .agent/agents/default.md）。"""
    return Session(
        session_id=session_id,
        history=build_history(storage, db_path, session_id),
        tool_config=ToolConfig(
            approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
        ),
        transport=transport or FakeChannel(),
    )


def make_registry(db_path: str) -> SessionRegistry:
    """构造带 Session 构造参数的 registry（register 测试用）。"""
    return SessionRegistry(
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
        assert isinstance(s._history, ProjectedMessages)
        assert isinstance(s._history.underlying, SQLiteMessages)
        assert not isinstance(s._history.underlying, InMemoryMessages)
        await s.close()

    async def test_memory_storage_when_configured(self, tmp_path):
        """显式配置 memory 时用内存存储。"""
        s = make_session("s1", str(tmp_path / "s.db"), storage="memory")
        assert isinstance(s._history, ProjectedMessages)
        assert isinstance(s._history.underlying, InMemoryMessages)
        await s.close()

    async def test_history_persists_across_session_instances(self, tmp_path):
        """历史跨 Session 实例存活（模拟断开重连后恢复）。"""
        db = str(tmp_path / "s.db")

        s1 = make_session("reconnect", db)
        await s1._history.add([UserMessage(content="hello")])
        await s1.close()

        # 重新连接：新 Session 实例，同一 db + session_id，历史仍在
        s2 = make_session("reconnect", db)
        msgs = await s2._history.get()
        assert len(msgs) == 1
        assert msgs[0].content == "hello"
        await s2.close()

    async def test_session_id_isolation(self, tmp_path):
        """不同 session_id 历史互不串扰（同一 db 文件）。"""
        db = str(tmp_path / "s.db")

        s1 = make_session("a", db)
        s2 = make_session("b", db)
        await s1._history.add([UserMessage(content="from-a")])
        await s2._history.add([UserMessage(content="from-b")])

        r1 = await s1._history.get()
        r2 = await s2._history.get()
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
        await s._history.add([UserMessage(content="after-detach")])
        msgs = await s._history.get()
        assert len(msgs) == 1

        # 重连：attach 新 transport，状态还在
        s.attach(FakeChannel())
        assert s.attached is True
        msgs = await s._history.get()
        assert msgs[0].content == "after-detach"
        await s.close()

    async def test_handle_requires_attach(self, tmp_path):
        """detached 会话调用 handle 报错（需先 attach）。"""
        from ..spec import UserInput

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
        from ..core import ExtensionAPI, ExtensionRegistry
        from ..spec import UserInput

        ch = FakeChannel()
        # 构造一个注册了 /ping 命令的扩展声明，激活进会话
        loader = ExtensionRegistry()

        async def load_cli(api: ExtensionAPI):
            from ..spec import Delta, DeltaEnd

            async def ping(args: str, ctx):
                # 推送由命令自己决定：经 channel notify Delta 回复流
                await ctx.channel.notify(Delta(delta="pong"))
                await ctx.channel.notify(DeltaEnd())

            api.register_command("ping", "ping", ping)

        loader.add("cli", load_cli)
        await loader.load()
        ext = loader.extensions[0]

        s = Session(
            session_id="cmd-1",
            history=build_history("memory", str(tmp_path / "s.db"), "cmd-1"),
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
            ),
            extensions=[ext],
            transport=ch,
        )

        # 命中命令：无 provider 也正常（不经 runner），回复被推送
        await s.handle(UserInput(content="/ping"))
        types = [type(e).__name__ for e in ch.events]
        assert types == ["Delta", "DeltaEnd"]  # 只有回复推送，无 agent 输出
        assert ch.events[0].delta == "pong"

        await s.close()

    async def test_silent_command_no_push(self, tmp_path):
        """静默命令（handler 返回 None）：命中但无任何推送。"""
        from ..core import ExtensionAPI, ExtensionRegistry
        from ..spec import UserInput

        ch = FakeChannel()
        loader = ExtensionRegistry()

        async def load_cli(api: ExtensionAPI):
            async def noop(args: str, ctx):
                return None  # 不调用 ctx → 静默执行

            api.register_command("noop", "noop", noop)

        loader.add("cli", load_cli)
        await loader.load()

        s = Session(
            session_id="cmd-2",
            history=build_history("memory", str(tmp_path / "s.db"), "cmd-2"),
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
            ),
            extensions=[loader.extensions[0]],
            transport=ch,
        )
        try:
            await s.handle(UserInput(content="/noop"))
            assert ch.events == []  # 无推送
        finally:
            await s.close()


class TestSessionModelConfig:
    """客户端随握手提供模型配置 → 会话级 provider（可指向不同端点）。"""

    class Conn:
        def __init__(self, chat_id, model=None):
            self.chat_id = chat_id
            self.cwd = None
            self.model = model

    def _registry(self, tmp_path):
        return SessionRegistry(
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
            ),
            storage="memory",
            db_path=str(tmp_path / "s.db"),
        )

    async def test_conn_model_creates_session_provider(self, tmp_path):
        reg = self._registry(tmp_path)
        cfg = ModelConfig(
            api_key="sk-a", base_url="https://a.example/v1", model="model-a"
        )
        s = reg.create(self.Conn("cfg-1", cfg))
        try:
            p = s.context.provider
            assert p is not None
            assert p.model == "model-a"
            # 客户端配置优先于进程默认 provider
            assert p is not reg._provider
        finally:
            await s.close()

    async def test_no_conn_model_uses_default(self, tmp_path):
        reg = self._registry(tmp_path)
        s = reg.create(self.Conn("cfg-2"))
        try:
            assert s.context.provider is reg._provider
        finally:
            await s.close()

    async def test_sessions_isolated_providers(self, tmp_path):
        """两个会话各自的模型配置互不影响。"""
        reg = self._registry(tmp_path)
        s1 = reg.create(
            self.Conn(
                "iso-1",
                ModelConfig(api_key="k1", base_url="https://a/v1", model="m1"),
            )
        )
        s2 = reg.create(
            self.Conn(
                "iso-2",
                ModelConfig(api_key="k2", base_url="https://b/v1", model="m2"),
            )
        )
        try:
            assert s1.context.provider.model == "m1"
            assert s2.context.provider.model == "m2"
            assert s1.context.provider is not s2.context.provider
        finally:
            await s1.close()
            await s2.close()


class TestSessionStreamForward:
    """Runner 只发事件，Session 监听 MESSAGE_DELTA/END 转发给 transport。"""

    def _session(self, tmp_path, ch: FakeChannel) -> Session:
        return Session(
            session_id="stream-1",
            history=build_history("memory", str(tmp_path / "s.db"), "stream-1"),
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
            ),
            transport=ch,
        )

    async def test_delta_and_end_forwarded_to_transport(self, tmp_path):
        ch = FakeChannel()
        s = self._session(tmp_path, ch)
        try:
            await s._bus.emit(MessageDeltaEvent(delta="你好"))
            await s._bus.emit(MessageEndEvent(output="你好", session_id=None))
            types = [type(e).__name__ for e in ch.events]
            assert types == ["Delta", "DeltaEnd"]
            assert ch.events[0].delta == "你好"
        finally:
            await s.close()

    async def test_tool_turn_no_delta_end(self, tmp_path):
        """MESSAGE_END output=None（工具轮/异常）→ 不发 DeltaEnd。"""
        ch = FakeChannel()
        s = self._session(tmp_path, ch)
        try:
            await s._bus.emit(MessageEndEvent(output=None, session_id=None))
            assert ch.events == []
        finally:
            await s.close()

    async def _registry(self, tmp_path) -> SessionRegistry:
        from ..core import ExtensionAPI, ExtensionRegistry
        from ..infra import Tool

        loader = ExtensionRegistry()

        async def ext_a(api: ExtensionAPI):
            async def fa(args):
                return "a"

            api.register_tool(
                Tool(name="tool_a", description="a", parameters={}, fn=fa)
            )
            api.on(AgentStartEvent, lambda evt: None)

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
                self.cwd = None
                self.model = None

        s1 = reg.create(Conn("s1"))  # 默认：全部扩展
        s2 = reg.create(Conn("s2"), ext_names=["ext_a"])  # 只激活 ext_a

        names1 = {t.name for t in s1.context.ext_runner.all_tools()}
        names2 = {t.name for t in s2.context.ext_runner.all_tools()}
        assert {"tool_a", "tool_b"} <= names1  # 会话 1 有全部
        assert names2 == {"tool_a"}  # 会话 2 只有 ext_a

        # 运行时隔离：各自 context / 扩展激活层独立（bus/工具表随会话）
        assert s1.context is not s2.context
        assert s1.context.ext_runner is not s2.context.ext_runner
        assert s1.context.ext_runner.active_names == {"ext_a", "ext_b"}
        assert s2.context.ext_runner.active_names == {"ext_a"}

        await reg.close()

    async def test_mcp_extension_tools_visible_in_session(self, tmp_path):
        """MCP 工具经扩展组装进会话：extension → 扩展声明 → 会话激活。"""
        from ..core import ExtensionRegistry
        from ..infra import Tool
        from ..tools.mcp import MCPRegistry

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

        mcp = MCPRegistry()
        mcp._clients.append(FakeMcpClient())

        # main 组装路径：MCPRegistry 作为扩展声明
        loader = ExtensionRegistry()
        loader.add("mcp", mcp.as_ext)
        await loader.load()
        mcp_ext = [e for e in loader.extensions if e.error is None]

        reg = SessionRegistry(
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
            ),
            extensions=mcp_ext,
            storage="memory",
            db_path=str(tmp_path / "s.db"),
        )

        class Conn:
            chat_id = "mcp-1"
            cwd = None
            model = None

        s = reg.create(Conn())
        names = {t.name for t in s.context.ext_runner.all_tools()}
        assert "mcp_remote_search" in names  # MCP 工具经扩展进会话工具表

        await reg.close()
