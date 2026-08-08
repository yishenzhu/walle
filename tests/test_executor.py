"""ToolExecutor 测试。"""
import json
import pytest

from ..conf import ApprovalConfig, ApprovalDecision, RawRule, ToolConfig
from ..core.executor import ToolExecutor
from ..schemas import ApprovalResponse
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
def ctx(channel):
    return ToolContext(channel=channel)


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

    async def test_execute_user_approves(self, ctx):
        executor = ToolExecutor(ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ASK)))
        ctx.channel.set_approval(approved=True)
        tool = make_tool("bash", "done")
        tc = make_tool_call(name="bash")
        tc_id, result = await executor.execute(tc, {"bash": tool}, ctx)
        assert result == "done"

    async def test_execute_user_denies(self, ctx):
        executor = ToolExecutor(ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ASK)))
        ctx.channel.set_approval(approved=False, reason="dangerous")
        tool = make_tool("bash", "done")
        tc = make_tool_call(name="bash")
        tc_id, result = await executor.execute(tc, {"bash": tool}, ctx)
        assert "denied by user" in result
        assert "dangerous" in result

    async def test_execute_ask_no_channel(self):
        from ..tools import ToolContext as TC
        ctx = TC(channel=None)
        executor = ToolExecutor(ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ASK)))
        tool = make_tool("bash")
        tc = make_tool_call(name="bash")
        tc_id, result = await executor.execute(tc, {"bash": tool}, ctx)
        assert "no channel" in result

    async def test_execute_timeout(self, ctx):
        import asyncio

        async def slow_fn(args):
            await asyncio.sleep(10)
            return "should not reach"

        tool = Tool(name="slow", description="d", parameters={"type": "object", "properties": {}}, fn=slow_fn)
        executor = ToolExecutor(ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW), timeout=0.1))
        tc = make_tool_call(name="slow")
        tc_id, result = await executor.execute(tc, {"slow": tool}, ctx)
        assert "timed out" in result

    async def test_execute_no_timeout_when_none(self, ctx):
        async def fn(args):
            return "ok"

        tool = Tool(name="ok", description="d", parameters={"type": "object", "properties": {}}, fn=fn)
        executor = ToolExecutor(ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW), timeout=None))
        tc = make_tool_call(name="ok")
        tc_id, result = await executor.execute(tc, {"ok": tool}, ctx)
        assert result == "ok"


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
