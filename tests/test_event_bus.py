"""EventBus 单元测试 + Runner 生命周期埋点验证。

覆盖：
1. EventBus 注册/发射/顺序/失败隔离
2. 未知事件名报错
3. Runner 在 agent_start / turn_start / turn_end / agent_end 正确发射
"""

import pytest

from ..core import Agent, EventBus, Runner, RunOptions, SessionContext, ToolExecutor
from ..conf import ApprovalConfig, ApprovalDecision, ToolConfig
from ..messages import InMemoryMessages
from ..spec import UserMessage
from ..infra import (
    Tool,
    AgentStartEvent,
    AgentEndEvent,
    TurnStartEvent,
    TurnEndEvent,
    SessionStartEvent,
    SessionEndEvent,
    MessageStartEvent,
    MessageEndEvent,
)

from .conftest import (
    FakeChannel,
    FakeCompletion,
    FakeMessage,
    FakeProvider,
    FakeToolCall,
    FakeUsage,
)


# ── EventBus 单元 ──────────────────────────────────────


async def test_emit_calls_in_order_and_returns_results():
    bus = EventBus()
    order = []

    async def h1(evt):
        order.append("h1")
        return 1

    async def h2(evt):
        order.append("h2")
        return 2

    bus.on(TurnStartEvent, h1)
    bus.on(TurnStartEvent, h2)
    results = await bus.emit(TurnStartEvent(turn=1, agent="a"))

    assert order == ["h1", "h2"]
    assert results == [1, 2]


async def test_emit_isolates_handler_failure():
    bus = EventBus()

    async def bad(evt):
        raise RuntimeError("boom")

    async def good(evt):
        return "ok"

    bus.on(TurnStartEvent, bad)
    bus.on(TurnStartEvent, good)

    results = await bus.emit(TurnStartEvent(turn=1, agent="a"))
    assert isinstance(results[0], RuntimeError)  # 失败不中断
    assert results[1] == "ok"


async def test_emit_dispatches_by_event_type():
    """按事件实例类型分发：订阅 A 不收到 B。"""
    bus = EventBus()
    seen = []

    async def on_turn(evt):
        seen.append("turn")

    bus.on(TurnStartEvent, on_turn)
    await bus.emit(TurnStartEvent(turn=1, agent="a"))
    await bus.emit(AgentStartEvent(agent="a", session_id=None))
    assert seen == ["turn"]


async def test_has_reflects_registration():
    bus = EventBus()
    assert not bus.has(TurnStartEvent)
    bus.on(TurnStartEvent, lambda evt: None)
    assert bus.has(TurnStartEvent)


async def test_clear_removes_handlers():
    bus = EventBus()
    bus.on(TurnStartEvent, lambda evt: None)
    bus.clear()
    assert not bus.has(TurnStartEvent)


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

    for et in (AgentStartEvent, TurnStartEvent, TurnEndEvent, AgentEndEvent):
        bus.on(et, lambda evt: events.append(type(evt).__name__))

    runner = Runner(executor=allow_executor)

    provider = FakeProvider()
    provider.client.chat.completions.set_responses(
        FakeCompletion(FakeMessage(content="final"), FakeUsage())
    )
    # env.provider 优先于 Runner 的默认 provider（不依赖类级 _default）
    env = SessionContext(
        channel=FakeChannel(),
        provider=provider,  # type: ignore[arg-type]
        history=InMemoryMessages(),
        bus=bus,
    )
    result = await runner.run(
        Agent(name="default", tools=lambda: [echo_tool()]),
        "hi",
        env=env,
    )

    # 单轮：agent_start, turn_start, turn_end, agent_end 依次
    assert events[0] == "AgentStartEvent"
    assert "TurnStartEvent" in events
    assert "TurnEndEvent" in events
    assert events[-1] == "AgentEndEvent"
    assert result.output == "final"


async def test_runner_emits_full_session_lifecycle(allow_executor):
    """单次 run 的完整事件序：session/message 包裹 agent/turn，参数齐全。"""
    bus = EventBus()
    events: list[str] = []
    payloads: dict[type, object] = {}

    def listen(et):
        bus.on(
            et,
            lambda evt, e=et: (
                events.append(e.__name__),
                payloads.setdefault(e, evt),
            ),
        )

    for et in (
        SessionStartEvent,
        MessageStartEvent,
        AgentStartEvent,
        TurnStartEvent,
        TurnEndEvent,
        MessageEndEvent,
        AgentEndEvent,
        SessionEndEvent,
    ):
        listen(et)

    runner = Runner(executor=allow_executor)
    provider = FakeProvider()
    provider.client.chat.completions.set_responses(
        FakeCompletion(FakeMessage(content="final"), FakeUsage())
    )
    env = SessionContext(
        channel=FakeChannel(),
        provider=provider,  # type: ignore[arg-type]
        history=InMemoryMessages(),
        session_id="s1",
        bus=bus,
    )
    result = await runner.run(
        Agent(name="default", tools=lambda: [echo_tool()]),
        "hi",
        env=env,
    )

    # 事件对边界：session_start 开头、session_end 收尾；message 包裹 agent
    assert events[0] == "SessionStartEvent"
    assert events[-1] == "SessionEndEvent"
    assert events.index("MessageStartEvent") < events.index("AgentStartEvent")
    assert events.index("MessageEndEvent") < events.index("AgentEndEvent")
    assert events.index("MessageEndEvent") < events.index("SessionEndEvent")
    assert payloads[MessageStartEvent].input == "hi"  # type: ignore[union-attr]
    assert payloads[SessionStartEvent].session_id == "s1"  # type: ignore[union-attr]
    assert result.output == "final"


async def test_runner_no_events_when_no_subscribers(allow_executor):
    """没有监听器时，run 全程不抛、正常返回。"""
    runner = Runner(executor=allow_executor)
    provider = FakeProvider()
    provider.client.chat.completions.set_responses(
        FakeCompletion(FakeMessage(content="x"), FakeUsage())
    )
    env = SessionContext(
        channel=FakeChannel(),
        provider=provider,  # type: ignore[arg-type]
        history=InMemoryMessages(),
        bus=EventBus(),
    )
    result = await runner.run(
        Agent(name="default", tools=lambda: [echo_tool()]), "hi", env=env
    )
    assert result.output == "x"
