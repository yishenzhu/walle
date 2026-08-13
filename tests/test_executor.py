"""ToolExecutor 测试。"""
import json
import pytest

from ..conf import ApprovalConfig, ApprovalDecision, RawRule, TimeoutConfig, ToolConfig
from ..core.executor import ToolExecutor
from ..schemas import ApprovalRsp
from ..tools import Tool, ToolContext

from .conftest import FakeChannel, FakeToolCall, FakeProvider


def make_tool(name, result="ok"):
    async def fn(args):
        return result

    return Tool(name=name, description=f"tool {name}", parameters={"type": "object", "properties": {}}, fn=fn)


def make_tool_call(id="tc1", name="echo", arguments=None):
    return FakeToolCall(id=id, name=name, arguments=json.dumps(arguments or {}))


@pytest.fixture
def provider():
    p = FakeProvider()
    FakeProvider.set_default(p)
    yield p
    FakeProvider.set_default(None)


@pytest.fixture
def channel():
    return FakeChannel()


@pytest.fixture
def ctx():
    return ToolContext()


class TestExecute:
    async def test_execute_allowed_tool(self, ctx):
        executor = ToolExecutor(ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW)))
        tool = make_tool("echo", "hello")
        tc = make_tool_call(name="echo")
        tc_id, result = await executor.execute(tc, {"echo": tool}, ctx)
        assert tc_id == "tc1"
        assert result == "hello"

    async def test_execute_denied_by_policy(self, ctx):
        config = ApprovalConfig(
            rules=[RawRule(ApprovalDecision.DENY, "bash")],
            default=ApprovalDecision.ALLOW,
        )
        executor = ToolExecutor(ToolConfig(approval=config))
        tool = make_tool("bash")
        tc = make_tool_call(name="bash")
        tc_id, result = await executor.execute(tc, {"bash": tool}, ctx)
        assert "denied by policy" in result

    async def test_execute_unknown_tool(self, ctx):
        executor = ToolExecutor(ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW)))
        tc = make_tool_call(name="nonexistent")
        tc_id, result = await executor.execute(tc, {}, ctx)
        assert "Unknown tool" in result

    async def test_execute_tool_exception(self, ctx):
        async def failing_fn(args):
            raise RuntimeError("boom")

        tool = Tool(name="boom", description="d", parameters={"type": "object", "properties": {}}, fn=failing_fn)
        executor = ToolExecutor(ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW)))
        tc = make_tool_call(name="boom")
        tc_id, result = await executor.execute(tc, {"boom": tool}, ctx)
        assert "Error: boom" in result

    async def test_execute_user_approves(self, channel):
        channel.set_approval(approved=True)
        executor = ToolExecutor(
            ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ASK))
        )
        tool = make_tool("bash", "done")
        tc = make_tool_call(name="bash")
        tc_id, result = await executor.execute(
            tc, {"bash": tool}, ToolContext(channel=channel)
        )
        assert result == "done"

    async def test_execute_user_denies(self, channel):
        channel.set_approval(approved=False, reason="dangerous")
        executor = ToolExecutor(
            ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ASK))
        )
        tool = make_tool("bash", "done")
        tc = make_tool_call(name="bash")
        tc_id, result = await executor.execute(
            tc, {"bash": tool}, ToolContext(channel=channel)
        )
        assert "denied by user" in result
        assert "dangerous" in result

    async def test_execute_ask_no_approver(self):
        executor = ToolExecutor(ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ASK)))
        tool = make_tool("bash")
        tc = make_tool_call(name="bash")
        tc_id, result = await executor.execute(tc, {"bash": tool}, ToolContext())
        assert "no approval channel" in result

    async def test_execute_timeout(self, ctx):
        import asyncio

        async def slow_fn(args):
            await asyncio.sleep(10)
            return "should not reach"

        tool = Tool(name="slow", description="d", parameters={"type": "object", "properties": {}}, fn=slow_fn)
        executor = ToolExecutor(ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW), timeout=TimeoutConfig(default=0.1)))
        tc = make_tool_call(name="slow")
        tc_id, result = await executor.execute(tc, {"slow": tool}, ctx)
        assert "timed out" in result

    async def test_execute_no_timeout_when_none(self, ctx):
        async def fn(args):
            return "ok"

        tool = Tool(name="ok", description="d", parameters={"type": "object", "properties": {}}, fn=fn)
        executor = ToolExecutor(ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW), timeout=TimeoutConfig(default=None)))
        tc = make_tool_call(name="ok")
        tc_id, result = await executor.execute(tc, {"ok": tool}, ctx)
        assert result == "ok"

    async def test_execute_timeout_overrides_global(self, ctx):
        import asyncio

        # 全局 0.1s 超时，但 ask_user 用单工具覆盖长超时
        async def slow_interactive(args):
            await asyncio.sleep(0.5)
            return "answered"

        tool = Tool(
            name="ask_user", description="d",
            parameters={"type": "object", "properties": {}}, fn=slow_interactive,
        )
        executor = ToolExecutor(
            ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
                timeout=TimeoutConfig(default=0.1, overrides={"ask_user": 5.0}),
            )
        )
        tc = make_tool_call(name="ask_user")
        tc_id, result = await executor.execute(tc, {"ask_user": tool}, ctx)
        assert result == "answered"   # 未被 0.1s 全局超时打断

    async def test_execute_timeout_exempt_with_none(self, ctx):
        import asyncio

        # 覆盖值为 None = 豁免超时：交互工具等用户回答不设时限
        async def interactive(args):
            await asyncio.sleep(0.5)
            return "answered"

        tool = Tool(
            name="ask_user", description="d",
            parameters={"type": "object", "properties": {}}, fn=interactive,
        )
        executor = ToolExecutor(
            ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
                timeout=TimeoutConfig(default=0.1, overrides={"ask_user": None}),
            )
        )
        tc = make_tool_call(name="ask_user")
        tc_id, result = await executor.execute(tc, {"ask_user": tool}, ctx)
        assert result == "answered"


class TestExecuteBatch:
    async def test_batch_multiple(self, ctx):
        executor = ToolExecutor(ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW)))
        tools = {
            "a": make_tool("a", "result_a"),
            "b": make_tool("b", "result_b"),
        }
        tcs = [make_tool_call(id="t1", name="a"), make_tool_call(id="t2", name="b")]
        results = await executor.execute_batch(tcs, tools, ctx)
        assert len(results) == 2
        result_map = dict(results)
        assert result_map["t1"] == "result_a"
        assert result_map["t2"] == "result_b"


class TestExecuteIter:
    async def test_iter_yields_all(self, ctx):
        executor = ToolExecutor(ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW)))
        tools = {
            "a": make_tool("a", "result_a"),
            "b": make_tool("b", "result_b"),
        }
        tcs = [make_tool_call(id="t1", name="a"), make_tool_call(id="t2", name="b")]
        results = []
        async for tc_id, result in executor.execute_iter(tcs, tools, ctx):
            results.append((tc_id, result))
        assert len(results) == 2
        ids = {tc_id for tc_id, _ in results}
        assert ids == {"t1", "t2"}
