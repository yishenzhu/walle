"""Session 存储持久化 + attach/detach 生命周期测试。"""

from pathlib import Path

import pytest

from ..conf import ApprovalConfig, ApprovalDecision, ApprovalRuleSpec, ToolConfig
from ..core import Session, SessionManager
from ..infra import AgentStartEvent, MessageDeltaEvent, MessageEndEvent
from ..messages import (
    FileMessages,
    InMemoryMessages,
    ProjectedMessages,
    SQLiteMessages,
    build_history,
)
from ..spec import ModelConfig, Sessions, UserMessage
from .conftest import FakeChannel, FakeProvider


def make_session(
    session_id: str,
    db_path: str,
    transport=None,
    storage="file",
    cwd=None,
):
    """构造一个 Session（agent 由内部按名加载 .agent/agents/default.md）。"""
    base = Path(db_path).parent
    path = base / "sessions" if storage == "file" else db_path
    return Session(
        session_id=session_id,
        history=build_history(
            storage,
            path,
            session_id,
            cwd=cwd or base / "workspace",
        ),
        tool_config=ToolConfig(
            approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
        ),
        transport=transport or FakeChannel(),
    )


def make_manager(db_path: str) -> SessionManager:
    """构造带 Session 构造参数的 manager（register 测试用）。"""
    return SessionManager(
        tool_config=ToolConfig(
            approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
        ),
        storage="memory",
        path=db_path,
    )


@pytest.fixture
def provider():
    p = FakeProvider()
    FakeProvider.set_default(p)
    yield p
    FakeProvider.set_default(None)


class TestSessionStorage:
    async def test_default_storage_is_file(self, tmp_path):
        """默认存储后端是工作目录文件（集中存储、跨连接持久化）。"""
        s = make_session("s1", str(tmp_path / "s.db"))
        assert isinstance(s._history, ProjectedMessages)
        assert isinstance(s._history.underlying, FileMessages)
        assert not isinstance(s._history.underlying, InMemoryMessages)
        await s.close()

    async def test_sqlite_storage_when_configured(self, tmp_path):
        """显式配置 sqlite 时仍使用数据库存储。"""
        s = make_session("s1", str(tmp_path / "s.db"), storage="sqlite")
        assert isinstance(s._history, ProjectedMessages)
        assert isinstance(s._history.underlying, SQLiteMessages)
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


class TestSessionMetadata:
    async def test_metadata_is_shared_with_context(self, tmp_path):
        s = Session(
            session_id="meta-1",
            history=build_history("memory", str(tmp_path / "s.db"), "meta-1"),
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
            ),
        )
        try:
            s.update_metadata({"course_id": "c1", "lesson_id": "l1"})
            assert s.context.metadata == {"course_id": "c1", "lesson_id": "l1"}

            s.update_metadata({"lesson_id": None})
            assert s.context.metadata == {"course_id": "c1"}
        finally:
            await s.close()


def test_tutor_agent_is_read_only():
    from ..core import Agent

    agent = Agent.load("tutor")
    assert agent.tool_filter.allows("read_course")
    assert not agent.tool_filter.allows("bash")
    assert not agent.tool_filter.allows("write_lesson")


def test_learning_agent_can_build_courses():
    from ..core import Agent

    agent = Agent.load("learning")
    assert agent.tool_filter.allows("create_course")
    assert agent.tool_filter.allows("write_lesson")
    assert agent.tool_filter.allows("course_canvas")
    assert not agent.tool_filter.allows("canvas")
    assert not agent.tool_filter.allows("bash")


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


class TestSessionManager:
    def test_satisfies_sessions_protocol(self, tmp_path):
        """协议声明的 get/create/list/浏览/close 必须由管理器完整实现。"""
        manager = make_manager(str(tmp_path / "s.db"))
        assert isinstance(manager, Sessions)

    async def test_create_uses_central_file_storage(self, tmp_path):
        """默认 file 后端：会话落在 path，而不是工作目录。"""
        sessions = tmp_path / "sessions"
        project = tmp_path / "project"
        manager = SessionManager(
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
            ),
            path=str(sessions),
        )

        class Conn:
            session_id = "file-1"
            cwd = str(project)
            model = None
            agent = None

        s = manager.create(Conn())
        assert isinstance(s._history.underlying, FileMessages)
        path = Path(s._history.underlying.path)
        assert path.is_relative_to(sessions)
        assert not path.is_relative_to(project)
        await manager.close()

    async def test_register_get_remove(self, tmp_path):
        manager = make_manager(str(tmp_path / "s.db"))
        s = make_session("session-1", str(tmp_path / "s.db"))
        manager.register(s)
        assert manager.get("session-1") is s
        assert manager.remove("session-1") is s
        assert manager.get("session-1") is None
        await s.close()

    async def test_duplicate_register_raises(self, tmp_path):
        manager = make_manager(str(tmp_path / "s.db"))
        s = make_session("dup", str(tmp_path / "s.db"))
        manager.register(s)
        with pytest.raises(ValueError):
            manager.register(s)
        await s.close()

    async def test_list_shows_attach_state(self, tmp_path):
        manager = make_manager(str(tmp_path / "s.db"))
        s = make_session("list-1", str(tmp_path / "s.db"))
        manager.register(s)
        s.detach()
        items = manager.list()
        assert len(items) == 1
        assert items[0]["session_id"] == "list-1"
        assert items[0]["attached"] is False
        assert items[0]["age_seconds"] >= 0
        await s.close()

    async def test_close(self, tmp_path):
        manager = make_manager(str(tmp_path / "s.db"))
        s1 = make_session("c1", str(tmp_path / "s.db"))
        s2 = make_session("c2", str(tmp_path / "s.db"))
        manager.register(s1)
        manager.register(s2)
        await manager.close()
        assert manager.list() == []


class TestSessionCommandDispatch:
    async def test_command_reply_bypasses_runner(self, tmp_path):
        """斜杠命令命中：handler 经 Channel 推送，不经 agent/runner。"""
        from ..plugin import plugin
        from ..spec import (
            AgentService,
            Channel,
            Command,
            CommandResult,
            CommandService,
            Delta,
            UserInput,
        )

        ch = FakeChannel()

        @plugin(name="cli", inject=(CommandService,))
        async def cli(ctx, config):
            async def ping(request, cctx):
                assert cctx.get(AgentService).current.name == "default"
                await cctx.get(Channel).notify(Delta(delta="pong"))
                return CommandResult()

            ctx.get(CommandService).register(
                Command(name="ping", description="ping", handler=ping)
            )

        s = Session(
            session_id="cmd-1",
            history=build_history("memory", str(tmp_path / "s.db"), "cmd-1"),
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
            ),
            plugins=[cli],
            transport=ch,
        )
        try:
            await s.start()
            await s.handle(UserInput(content="/ping"))
            types = [type(e).__name__ for e in ch.events]
            assert types == ["Delta", "DeltaEnd"]
            assert ch.events[0].delta == "pong"
        finally:
            await s.close()

    async def test_silent_command_ends_turn(self, tmp_path):
        """静默命令只发送 DeltaEnd，不产生回复文本。"""
        from ..plugin import plugin
        from ..spec import Command, CommandResult, CommandService, UserInput

        ch = FakeChannel()

        @plugin(name="cli", inject=(CommandService,))
        async def cli(ctx, config):
            async def noop(request, cctx):
                return CommandResult()

            ctx.get(CommandService).register(
                Command(name="noop", description="noop", handler=noop)
            )

        s = Session(
            session_id="cmd-2",
            history=build_history("memory", str(tmp_path / "s.db"), "cmd-2"),
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
            ),
            plugins=[cli],
            transport=ch,
        )
        try:
            await s.start()
            await s.handle(UserInput(content="/noop"))
            assert [type(e).__name__ for e in ch.events] == ["DeltaEnd"]
        finally:
            await s.close()


class TestSessionModelConfig:
    """客户端随握手提供模型配置 → 会话级 provider（可指向不同端点）。"""

    class Conn:
        def __init__(self, session_id, model=None):
            self.session_id = session_id
            self.cwd = None
            self.model = model
            self.agent = None

    def _manager(self, tmp_path):
        return SessionManager(
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
            ),
            storage="memory",
            path=str(tmp_path / "s.db"),
        )

    async def test_conn_model_creates_session_provider(self, tmp_path):
        manager = self._manager(tmp_path)
        cfg = ModelConfig(
            api_key="sk-a", base_url="https://a.example/v1", model="model-a"
        )
        s = manager.create(self.Conn("cfg-1", cfg))
        try:
            p = s.context.provider
            assert p is not None
            assert p.model == "model-a"
            # 客户端配置优先于进程默认 provider
            assert p is not manager._provider
        finally:
            await s.close()

    async def test_no_conn_model_uses_default(self, tmp_path):
        manager = self._manager(tmp_path)
        s = manager.create(self.Conn("cfg-2"))
        try:
            assert s.context.provider is manager._provider
        finally:
            await s.close()

    async def test_sessions_isolated_providers(self, tmp_path):
        """两个会话各自的模型配置互不影响。"""
        manager = self._manager(tmp_path)
        s1 = manager.create(
            self.Conn(
                "iso-1",
                ModelConfig(api_key="k1", base_url="https://a/v1", model="m1"),
            )
        )
        s2 = manager.create(
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

    async def test_empty_output_still_ends_stream(self, tmp_path):
        """MESSAGE_END output=None 也是 run 终态 → 必须发 DeltaEnd（防客户端挂起）。"""
        ch = FakeChannel()
        s = self._session(tmp_path, ch)
        try:
            await s._bus.emit(MessageEndEvent(output=None, session_id=None))
            assert [type(e).__name__ for e in ch.events] == ["DeltaEnd"]
            assert ch.events[0].reason is None
        finally:
            await s.close()

    async def _manager(self, tmp_path) -> SessionManager:
        from ..core import PluginAPI, PluginManager
        from ..infra import Tool

        loader = PluginManager()

        async def plugin_a(api: PluginAPI):
            async def fa(args):
                return "a"

            api.register_tool(
                Tool(name="tool_a", description="a", parameters={}, fn=fa)
            )
            api.on(AgentStartEvent, lambda evt: None)

        async def plugin_b(api: PluginAPI):
            async def fb(args):
                return "b"

            api.register_tool(
                Tool(name="tool_b", description="b", parameters={}, fn=fb)
            )

        loader.add("plugin_a", plugin_a)
        loader.add("plugin_b", plugin_b)
        await loader.load()
        pool = [e for e in loader.plugins if e.error is None]

        manager = SessionManager(
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
            ),
            declarations=pool,
            storage="memory",
            path=str(tmp_path / "s.db"),
        )
        return manager

    async def test_sessions_activate_different_plugins(self, tmp_path):
        """会话 1 用全部插件；会话 2 只激活 plugin_a——工具表不同、事件隔离。"""
        manager = await self._manager(tmp_path)

        class Conn:
            def __init__(self, session_id):
                self.session_id = session_id
                self.cwd = None
                self.model = None
                self.agent = None

        s1 = manager.create(Conn("s1"))  # 默认：全部插件
        s2 = manager.create(Conn("s2"), plugin_names=["plugin_a"])  # 只激活 plugin_a

        names1 = {t.name for t in s1.context.plugin_runner.all_tools()}
        names2 = {t.name for t in s2.context.plugin_runner.all_tools()}
        assert {"tool_a", "tool_b"} <= names1  # 会话 1 有全部
        assert names2 == {"tool_a"}  # 会话 2 只有 plugin_a

        # 运行时隔离：各自 context / 插件激活层独立（bus/工具表随会话）
        assert s1.context is not s2.context
        assert s1.context.plugin_runner is not s2.context.plugin_runner
        assert s1.context.plugin_runner.active_plugins == {"plugin_a", "plugin_b"}
        assert s2.context.plugin_runner.active_plugins == {"plugin_a"}

        await manager.close()

    async def test_new_plugins_start_into_session(self, tmp_path):
        """新插件经 PluginHost 启动，工具落到会话工具表。"""
        from ..tools.builtin.plugin import builtin_plugin

        s = Session(
            session_id="plugin-1",
            history=build_history("memory", str(tmp_path / "s.db"), "plugin-1"),
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
            ),
            plugins=[builtin_plugin],
        )
        try:
            await s.start()
            names = {t.name for t in s.context.plugin_runner.all_tools()}
            assert {"bash", "read", "write"} <= names
            assert s.context.prompt is not None  # 插件 prompt 服务已接入
        finally:
            await s.close()

    async def test_new_approval_plugin_blocks_legacy_executor(self, tmp_path):
        """新插件总线经桥接生效：approval 插件能拦住旧 ToolExecutor。"""
        from ..infra import Tool, tool_context
        from ..tools.approval import native_approval

        s = Session(
            session_id="plugin-approval",
            history=build_history("memory", str(tmp_path / "s.db"), "plugin-approval"),
            tool_config=ToolConfig(
                approval=ApprovalConfig(
                    rules=[ApprovalRuleSpec(ApprovalDecision.DENY, "bash")],
                    default=ApprovalDecision.ALLOW,
                )
            ),
            plugins=[native_approval],
        )
        try:
            await s.start()

            async def fn(args):
                return "ran"

            tool = Tool(name="bash", description="b", parameters={}, fn=fn)
            token = tool_context.set(s.context)
            try:
                _, result = await s._agent_runner._executor.execute_tool(
                    "bash", {}, "tc1", {"bash": tool}
                )
            finally:
                tool_context.reset(token)
            assert "denied by policy" in result
        finally:
            await s.close()

    async def test_mcp_pluginension_tools_visible_in_session(self, tmp_path):
        """MCP 工具经插件组装进会话：plugin → 插件声明 → 会话激活。"""
        from ..core import PluginManager
        from ..infra import Tool
        from ..tools.mcp import MCPManager

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

        mcp = MCPManager()
        mcp._clients.append(FakeMcpClient())

        # main 组装路径：MCPManager 作为插件声明
        loader = PluginManager()
        loader.add("mcp", mcp.legacy_plugin)
        await loader.load()
        mcp_plugin = [e for e in loader.plugins if e.error is None]

        manager = SessionManager(
            tool_config=ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW)
            ),
            declarations=mcp_plugin,
            storage="memory",
            path=str(tmp_path / "s.db"),
        )

        class Conn:
            session_id = "mcp-1"
            cwd = None
            model = None
            agent = None

        s = manager.create(Conn())
        names = {t.name for t in s.context.plugin_runner.all_tools()}
        assert "mcp_remote_search" in names  # MCP 工具经插件进会话工具表

        await manager.close()
