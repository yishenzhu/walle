"""EventBus 单元测试 + Runner 生命周期埋点验证。

覆盖：
1. EventBus 注册/发射/顺序/失败隔离
2. 未知事件名报错
3. Runner 在 agent_start / turn_start / turn_end / agent_end 正确发射
"""

import pytest

from ..core import Agent, EventBus, Runner, RunOptions, SessionEnv, ToolExecutor
from ..conf import ApprovalConfig, ApprovalDecision, ToolConfig
from ..messages import InMemoryMessages
from ..schemas import UserMessage
from ..tools import Tool

from .conftest import (
    FakeChannel,
    FakeCompletion,
    FakeMessage,
    FakeProvider,
    FakeToolCall,
    FakeUsage,
)


# ── EventBus 单元 ──────────────────────────────────────


def test_on_unknown_event_raises():
    bus = EventBus()
    with pytest.raises(ValueError):
        bus.on("nope", lambda **kw: None)


async def test_emit_calls_in_order_and_returns_results():
    bus = EventBus()
    order = []

    async def h1(**kw):
        order.append("h1")
        return 1

    async def h2(**kw):
        order.append("h2")
        return 2

    bus.on("turn_start", h1)
    bus.on("turn_start", h2)
    results = await bus.emit("turn_start", turn=1)

    assert order == ["h1", "h2"]
    assert results == [1, 2]


async def test_emit_isolates_handler_failure():
    bus = EventBus()

    async def bad(**kw):
        raise RuntimeError("boom")

    async def good(**kw):
        return "ok"

    bus.on("turn_start", bad)
    bus.on("turn_start", good)

    results = await bus.emit("turn_start", turn=1)
    assert isinstance(results[0], RuntimeError)  # 失败不中断
    assert results[1] == "ok"


async def test_has_reflects_registration():
    bus = EventBus()
    assert not bus.has("turn_start")
    bus.on("turn_start", lambda **kw: None)
    assert bus.has("turn_start")


async def test_clear_removes_handlers():
    bus = EventBus()
    bus.on("turn_start", lambda **kw: None)
    bus.clear()
    assert not bus.has("turn_start")


# ── Runner 生命周期埋点 ────────────────────────────────


@pytest.fixture
def allow_executor():
    return ToolExecutor(
        ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
    )


def echo_tool():
    async def fn(args):
        return args.get("v", "echoed")

    return Tool(
        name="echo", description="echo", parameters={"v": {"type": "string"}}, fn=fn
    )


async def test_runner_emits_lifecycle(allow_executor):
    bus = EventBus()
    events = []

    for e in ("agent_start", "turn_start", "turn_end", "agent_end"):
        bus.on(e, lambda ev=e, **kw: events.append(ev))

    runner = Runner(executor=allow_executor, bus=bus)

    provider = FakeProvider()
    provider.client.chat.completions.set_responses(
        FakeCompletion(FakeMessage(content="final"), FakeUsage())
    )
    # env.provider 优先于 Runner 的默认 provider（不依赖类级 _default）
    env = SessionEnv(
        channel=FakeChannel(),
        provider=provider,  # type: ignore[arg-type]
        messages=InMemoryMessages(),
    )
    result = await runner.run(
        Agent(name="default", tools=lambda: [echo_tool()]),
        "hi",
        env=env,
    )

    # 单轮：agent_start, turn_start, turn_end, agent_end 依次
    assert events[0] == "agent_start"
    assert "turn_start" in events
    assert "turn_end" in events
    assert events[-1] == "agent_end"
    assert result.output == "final"


async def test_runner_no_events_when_no_subscribers(allow_executor):
    """没有监听器时，run 全程不抛、正常返回。"""
    runner = Runner(executor=allow_executor, bus=EventBus())
    provider = FakeProvider()
    provider.client.chat.completions.set_responses(
        FakeCompletion(FakeMessage(content="x"), FakeUsage())
    )
    env = SessionEnv(
        channel=FakeChannel(),
        provider=provider,  # type: ignore[arg-type]
        messages=InMemoryMessages(),
    )
    result = await runner.run(
        Agent(name="default", tools=lambda: [echo_tool()]), "hi", env=env
    )
    assert result.output == "x"
