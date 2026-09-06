"""ToolExecutor 测试。"""

import json
import pytest

from ..conf import ApprovalConfig, ApprovalDecision, RawRule, TimeoutConfig, ToolConfig
from ..core.executor import ToolExecutor
from ..schemas import ApprovalRsp
from ..infra import Tool, ToolContext

from .conftest import FakeChannel, FakeToolCall, FakeProvider


def make_tool(name, result="ok"):
    async def fn(args):
        return result

    return Tool(
        name=name,
        description=f"tool {name}",
        parameters={"type": "object", "properties": {}},
        fn=fn,
    )


def make_tool_call(id="tc1", name="echo", arguments=None):
    return FakeToolCall(id=id, name=name, arguments=json.dumps(arguments or {}))


async def mount_approval(ctx, config) -> None:
    """把审批扩展（按 conf 规则）激活到 ctx.bus。

    审批不再由 executor 内置——测试经扩展验证 deny/ask 语义。
    """
    from ..core import EventBus, ExtensionRegistry, ExtensionRunner
    from ..infra import tool_context
    from ..tools.approval import Approval

    loader = ExtensionRegistry()
    loader.add("approval", Approval(config).as_ext)
    await loader.load()
    assert loader.extensions[0].error is None
    if ctx.bus is None:
        ctx.bus = EventBus()
    ExtensionRunner(ctx.bus).activate(loader.extensions[0])
    # 模拟 runner 每轮统一注入：tool_context 由调用方设置，executor 不再自设
    tool_context.set(ctx)


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
        executor = ToolExecutor(
            ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
        )
        tool = make_tool("echo", "hello")
        tc = make_tool_call(name="echo")
        tc_id, result = await executor.execute_call(tc, {"echo": tool})
        assert tc_id == "tc1"
        assert result == "hello"

    async def test_execute_denied_by_policy(self):
        config = ApprovalConfig(
            rules=[RawRule(ApprovalDecision.DENY, "bash")],
            default=ApprovalDecision.ALLOW,
        )
        ctx = ToolContext()
        await mount_approval(ctx, config)
        executor = ToolExecutor()
        tool = make_tool("bash")
        tc = make_tool_call(name="bash")
        tc_id, result = await executor.execute_call(tc, {"bash": tool})
        assert "denied by policy" in result

    async def test_execute_unknown_tool(self, ctx):
        executor = ToolExecutor(
            ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
        )
        tc = make_tool_call(name="nonexistent")
        tc_id, result = await executor.execute_call(tc, {})
        assert "Unknown tool" in result

    async def test_execute_tool_exception(self, ctx):
        async def failing_fn(args):
            raise RuntimeError("boom")

        tool = Tool(
            name="boom",
            description="d",
            parameters={"type": "object", "properties": {}},
            fn=failing_fn,
        )
        executor = ToolExecutor(
            ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
        )
        tc = make_tool_call(name="boom")
        tc_id, result = await executor.execute_call(tc, {"boom": tool})
        assert "Error: boom" in result

    async def test_execute_user_approves(self, channel):
        channel.set_approval(approved=True)
        ctx = ToolContext(channel=channel)
        await mount_approval(ctx, ApprovalConfig(default=ApprovalDecision.ASK))
        executor = ToolExecutor()
        tool = make_tool("bash", "done")
        tc = make_tool_call(name="bash")
        tc_id, result = await executor.execute_call(
            tc, {"bash": tool}
        )
        assert result == "done"

    async def test_execute_user_denies(self, channel):
        channel.set_approval(approved=False, reason="dangerous")
        ctx = ToolContext(channel=channel)
        await mount_approval(ctx, ApprovalConfig(default=ApprovalDecision.ASK))
        executor = ToolExecutor()
        tool = make_tool("bash", "done")
        tc = make_tool_call(name="bash")
        tc_id, result = await executor.execute_call(
            tc, {"bash": tool}
        )
        assert "denied by user" in result
        assert "dangerous" in result

    async def test_execute_ask_no_approver(self):
        ctx = ToolContext()
        await mount_approval(ctx, ApprovalConfig(default=ApprovalDecision.ASK))
        executor = ToolExecutor()
        tool = make_tool("bash")
        tc = make_tool_call(name="bash")
        tc_id, result = await executor.execute_call(tc, {"bash": tool})
        assert "no approval channel" in result

    async def test_execute_timeout(self, ctx):
        import asyncio

        async def slow_fn(args):
            await asyncio.sleep(10)
            return "should not reach"

        tool = Tool(
            name="slow",
            description="d",
            parameters={"type": "object", "properties": {}},
            fn=slow_fn,
        )
        executor = ToolExecutor(
            ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
                timeout=TimeoutConfig(default=0.1),
            )
        )
        tc = make_tool_call(name="slow")
        tc_id, result = await executor.execute_call(tc, {"slow": tool})
        assert "timed out" in result

    async def test_execute_no_timeout_when_none(self, ctx):
        async def fn(args):
            return "ok"

        tool = Tool(
            name="ok",
            description="d",
            parameters={"type": "object", "properties": {}},
            fn=fn,
        )
        executor = ToolExecutor(
            ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
                timeout=TimeoutConfig(default=None),
            )
        )
        tc = make_tool_call(name="ok")
        tc_id, result = await executor.execute_call(tc, {"ok": tool})
        assert result == "ok"

    async def test_execute_timeout_overrides_global(self, ctx):
        import asyncio

        # 全局 0.1s 超时，但 ask_user 用单工具覆盖长超时
        async def slow_interactive(args):
            await asyncio.sleep(0.5)
            return "answered"

        tool = Tool(
            name="ask_user",
            description="d",
            parameters={"type": "object", "properties": {}},
            fn=slow_interactive,
        )
        executor = ToolExecutor(
            ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
                timeout=TimeoutConfig(default=0.1, overrides={"ask_user": 5.0}),
            )
        )
        tc = make_tool_call(name="ask_user")
        tc_id, result = await executor.execute_call(tc, {"ask_user": tool})
        assert result == "answered"  # 未被 0.1s 全局超时打断

    async def test_execute_timeout_exempt_with_none(self, ctx):
        import asyncio

        # 覆盖值为 None = 豁免超时：交互工具等用户回答不设时限
        async def interactive(args):
            await asyncio.sleep(0.5)
            return "answered"

        tool = Tool(
            name="ask_user",
            description="d",
            parameters={"type": "object", "properties": {}},
            fn=interactive,
        )
        executor = ToolExecutor(
            ToolConfig(
                approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
                timeout=TimeoutConfig(default=0.1, overrides={"ask_user": None}),
            )
        )
        tc = make_tool_call(name="ask_user")
        tc_id, result = await executor.execute_call(tc, {"ask_user": tool})
        assert result == "answered"


class TestExecuteCalls:
    """execute_calls：并发执行一批工具调用，按完成序产出（async for 或收集）。"""

    def _executor(self) -> ToolExecutor:
        return ToolExecutor(
            ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
        )

    def _tools_tcs(self):
        tools = {
            "a": make_tool("a", "result_a"),
            "b": make_tool("b", "result_b"),
        }
        tcs = [make_tool_call(id="t1", name="a"), make_tool_call(id="t2", name="b")]
        return tools, tcs

    async def test_calls_yield_all(self):
        executor = self._executor()
        tools, tcs = self._tools_tcs()
        results = [r async for r in executor.execute_calls(tcs, tools)]
        assert len(results) == 2
        result_map = dict(results)
        assert result_map["t1"] == "result_a"
        assert result_map["t2"] == "result_b"

    async def test_calls_iterate_inline(self):
        executor = self._executor()
        tools, tcs = self._tools_tcs()
        results = []
        async for tc_id, result in executor.execute_calls(tcs, tools):
            results.append((tc_id, result))
        assert {tc_id for tc_id, _ in results} == {"t1", "t2"}


class TestToolHooks:
    """工具执行钩子（before/after）屏障。"""

    def test_empty_hook_verdict_rejected(self):
        """空 HookVerdict()（不表态）是编程错误，构造即抛 ValueError。"""
        import pytest

        from ..core import HookVerdict

        with pytest.raises(ValueError):
            HookVerdict()

    async def test_before_hook_pass_executes(self, ctx):
        from ..core import EventBus
        from ..infra import ToolExecutionStartEvent

        executor = ToolExecutor(
            ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
        )

        async def allow(evt):
            return True

        bus = EventBus()
        bus.on(ToolExecutionStartEvent, allow)
        ctx.bus = bus
        from ..infra import tool_context
        tool_context.set(ctx)  # executor 从 tool_context 取会话上下文

        tool = make_tool("echo", "ran")
        tc_id, result = await executor.execute_call(
            make_tool_call(name="echo"), {"echo": tool}
        )
        assert result == "ran"

    async def test_preflight_hook_sees_tool_context(self, ctx, channel):
        """preflight 钩子执行时 tool_context 已注入：handler 可拿会话上下文交互。"""
        from ..core import EventBus
        from ..infra import ToolExecutionStartEvent
        from ..infra import tool_context

        executor = ToolExecutor(
            ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
        )

        seen = {}

        async def check(evt):
            seen["ctx"] = tool_context.get()  # 事件 handler 内经上下文拿交互接口

        bus = EventBus()
        bus.on(ToolExecutionStartEvent, check)
        ctx.channel = channel
        ctx.bus = bus
        from ..infra import tool_context
        tool_context.set(ctx)  # executor 从 tool_context 取会话上下文
        tool_context.set(ctx)  # 模拟 runner 每轮统一注入

        tool = make_tool("echo", "ran")
        await executor.execute_call(make_tool_call(name="echo"), {"echo": tool})
        assert seen["ctx"] is ctx  # handler 拿到的是本次执行的上下文（含 channel）

    async def test_after_hook_notified(self, ctx):
        from ..core import EventBus
        from ..infra import ToolExecutionEndEvent

        executor = ToolExecutor(
            ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
        )

        seen = []

        async def record(evt):
            seen.append(evt.tool_name)

        bus = EventBus()
        bus.on(ToolExecutionEndEvent, record)
        ctx.bus = bus
        from ..infra import tool_context
        tool_context.set(ctx)  # executor 从 tool_context 取会话上下文

        tool = make_tool("echo", "ran")
        await executor.execute_call(make_tool_call(name="echo"), {"echo": tool})
        assert seen == ["echo"]

    async def test_no_bus_skips_hooks(self, ctx):
        executor = ToolExecutor(
            ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
        )
        tool = make_tool("echo", "ran")
        tc_id, result = await executor.execute_call(
            make_tool_call(name="echo"), {"echo": tool}
        )
        assert result == "ran"  # ctx.bus 为 None，钩子跳过

    async def test_before_hook_block_with_reason(self, ctx):
        """TOOL_EXECUTION_START 监听器返回 HookVerdict(block=reason) → 阻止执行。"""
        from ..core import EventBus
        from ..infra import ToolExecutionStartEvent, HookVerdict

        executor = ToolExecutor(
            ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
        )

        async def block(evt):
            return HookVerdict(block="危险命令")

        bus = EventBus()
        bus.on(ToolExecutionStartEvent, block)
        ctx.bus = bus
        from ..infra import tool_context
        tool_context.set(ctx)  # executor 从 tool_context 取会话上下文

        tool = make_tool("echo", "should-not-run")
        tc_id, result = await executor.execute_call(
            make_tool_call(name="echo"), {"echo": tool}
        )
        assert tc_id == "tc1"
        assert "blocked by extension" in result
        assert "危险命令" in result  # reason 透传给模型

    async def test_before_hook_rewrites_arguments(self, ctx):
        """TOOL_EXECUTION_START 监听器返回 HookVerdict(arguments=...) → 改写本次调用。"""
        from ..core import EventBus
        from ..infra import ToolExecutionStartEvent, HookVerdict

        executor = ToolExecutor(
            ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
        )

        seen = {}

        async def rewrite(evt):
            return HookVerdict(arguments={"command": "cd /repo && ls"})

        async def echo(args):
            seen.update(args)
            return "done"

        bus = EventBus()
        bus.on(ToolExecutionStartEvent, rewrite)
        ctx.bus = bus
        from ..infra import tool_context
        tool_context.set(ctx)  # executor 从 tool_context 取会话上下文

        tool = Tool(
            name="bash",
            description="bash",
            parameters={"type": "object", "properties": {}},
            fn=echo,
        )
        tc_id, result = await executor.execute_call(
            make_tool_call(name="bash", arguments={"command": "ls"}), {"bash": tool}
        )
        assert result == "done"
        assert seen == {"command": "cd /repo && ls"}  # 工具收到改写后的参数

    async def test_after_hook_receives_result(self, ctx):
        """TOOL_EXECUTION_END 携带执行结果 / 耗时（观测型扩展可用）。"""
        from ..core import EventBus
        from ..infra import ToolExecutionEndEvent

        executor = ToolExecutor(
            ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
        )

        seen = {}

        async def record(evt):
            seen["result"] = evt.result
            seen["error"] = evt.error
            seen["elapsed_ms"] = evt.elapsed_ms

        bus = EventBus()
        bus.on(ToolExecutionEndEvent, record)
        ctx.bus = bus
        from ..infra import tool_context
        tool_context.set(ctx)  # executor 从 tool_context 取会话上下文

        tool = make_tool("echo", "hello")
        await executor.execute_call(make_tool_call(name="echo"), {"echo": tool})
        assert seen["result"] == "hello"
        assert seen["error"] is None
        assert seen["elapsed_ms"] is not None
