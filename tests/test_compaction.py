"""Compaction 压缩框架测试：policy 判断 + compressor 注入 + 投影写回。"""

from ..core import EventBus, ExtensionRegistry, ExtensionRunner
from ..infra import TurnEndEvent
from ..messages import (
    Compaction,
    ProjectedMessages,
    InMemoryMessages,
    PromptLimitPolicy,
    PROMPT_LIMIT,
)
from ..schemas import UserMessage, Usage


class FakeCompressor:
    """可编程 compressor：记录收到的消息段/provider，返回可配置摘要。"""

    def __init__(self, summary="summary text"):
        self.summary = summary
        self.calls: list[list] = []
        self.providers: list = []

    async def __call__(self, items, *, provider=None):
        self.calls.append(list(items))
        self.providers.append(provider)
        return self.summary


async def activate_compaction(bus, compressor=None, **compaction_kwargs):
    """把 Compaction 框架（注入 policy+compressor）挂到 bus。"""
    mgr = ExtensionRegistry()
    comp = Compaction(
        policy=PromptLimitPolicy(),
        compressor=compressor or FakeCompressor(),
        **compaction_kwargs,
    )

    async def factory(api):
        await comp.as_ext(api)

    mgr.add("compaction", factory)
    await mgr.load()
    runner = ExtensionRunner(bus)
    runner.activate(*mgr.extensions)
    return comp


def over_limit_usage():
    return Usage(
        prompt_tokens=PROMPT_LIMIT + 100,
        completion_tokens=10,
        total_tokens=PROMPT_LIMIT + 110,
    )


def under_limit_usage():
    return Usage(prompt_tokens=100, completion_tokens=10, total_tokens=110)


async def emit_turn_end(bus, history, usage):
    await bus.emit(
        TurnEndEvent(
            turn=1,
            agent="default",
            session_id="s1",
            history=history,
            usage=usage,
            provider=None,
        )
    )


async def test_compacts_when_over_threshold():
    bus = EventBus()
    comp = FakeCompressor(summary="summary text")
    await activate_compaction(bus, compressor=comp)
    history = ProjectedMessages(InMemoryMessages())
    await history.add([UserMessage(content=f"u{i}") for i in range(8)])

    await emit_turn_end(bus, history, over_limit_usage())

    projected = await history.get()
    # 8 条 → keep_recent=4 折叠前 4 条为摘要，保留 4 条原文
    assert len(projected) == 5
    assert projected[0].role == "system"
    assert projected[0].content == "summary text"
    assert [m.content for m in projected[1:]] == ["u4", "u5", "u6", "u7"]
    # 底层原文全量保留
    raw = await history.underlying.get()
    assert len(raw) == 8
    # compressor 收到的是切点前的 4 条
    assert [m.content for m in comp.calls[0]] == ["u0", "u1", "u2", "u3"]


async def test_skips_when_under_threshold():
    bus = EventBus()
    comp = FakeCompressor()
    await activate_compaction(bus, compressor=comp)
    history = ProjectedMessages(InMemoryMessages())
    await history.add([UserMessage(content=f"u{i}") for i in range(8)])

    await emit_turn_end(bus, history, under_limit_usage())

    # 未触发：compressor 未被调用、投影未设
    assert comp.calls == []
    projected = await history.get()
    assert len(projected) == 8


async def test_skips_when_history_not_projected():
    """history 是裸存储（无 set_projection）时安全跳过，不报错。"""
    bus = EventBus()
    comp = FakeCompressor()
    await activate_compaction(bus, compressor=comp)
    raw_storage = InMemoryMessages()
    await raw_storage.add([UserMessage(content=f"u{i}") for i in range(8)])

    await emit_turn_end(bus, raw_storage, over_limit_usage())

    assert comp.calls == []
    assert len(await raw_storage.get()) == 8


async def test_keep_recent_configurable():
    bus = EventBus()
    comp = FakeCompressor()
    await activate_compaction(bus, compressor=comp, keep_recent=2)
    history = ProjectedMessages(InMemoryMessages())
    await history.add([UserMessage(content=f"u{i}") for i in range(6)])

    await emit_turn_end(bus, history, over_limit_usage())

    projected = await history.get()
    # 6 条 → keep_recent=2 折叠前 4 条，保留 2 条
    assert len(projected) == 3
    assert projected[0].content == "summary text"
    assert [m.content for m in projected[1:]] == ["u4", "u5"]


async def test_short_history_never_compresses():
    """历史不足 keep_recent 时即使超阈值也不压。"""
    bus = EventBus()
    comp = FakeCompressor()
    await activate_compaction(bus, compressor=comp, keep_recent=4)
    history = ProjectedMessages(InMemoryMessages())
    await history.add([UserMessage(content=f"u{i}") for i in range(3)])

    await emit_turn_end(bus, history, over_limit_usage())

    assert comp.calls == []
    projected = await history.get()
    assert len(projected) == 3


async def test_summarize_none_keeps_current_projection():
    """compressor 返回 None 时保持现状，不覆盖已有投影。"""
    bus = EventBus()
    comp = FakeCompressor(summary=None)
    await activate_compaction(bus, compressor=comp)
    history = ProjectedMessages(InMemoryMessages())
    await history.add([UserMessage(content=f"u{i}") for i in range(8)])
    await history.set_projection(2, "previous summary")

    await emit_turn_end(bus, history, over_limit_usage())

    projected = await history.get()
    assert projected[0].content == "previous summary"


async def test_end_to_end_via_runner_turn_end():
    """真实 Runner 跑一轮：TURN_END 触发压缩扩展，投影自动生效。"""
    from ..core import Runner, SessionContext, ToolExecutor, Agent
    from ..conf import ApprovalConfig, ApprovalDecision, ToolConfig
    from .conftest import FakeCompletion, FakeMessage, FakeProvider

    provider = FakeProvider()
    FakeProvider.set_default(provider)
    try:
        bus = EventBus()
        comp = FakeCompressor(summary="summary text")
        await activate_compaction(bus, compressor=comp, keep_recent=2)

        history = ProjectedMessages(InMemoryMessages())
        # 预置足量历史：跑本轮前已有 8 条（本轮还会追加 user+assistant）
        for i in range(8):
            await history.add([UserMessage(content=f"old{i}")])

        runner = Runner(
            executor=ToolExecutor(
                ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
            ),
            bus=bus,
        )
        # 超阈值 usage：本轮 TURN_END 触发压缩
        big_usage = Usage(
            prompt_tokens=PROMPT_LIMIT + 500,
            completion_tokens=10,
            total_tokens=PROMPT_LIMIT + 510,
        )
        provider.client.chat.completions.set_responses(
            FakeCompletion(FakeMessage(content="hi there"), big_usage)
        )

        result = await runner.run(
            Agent(name="default", instruction="be brief"),
            "hello",
            env=SessionContext(
                channel=None,
                provider=provider,  # type: ignore[arg-type]
                messages=history,
                session_id="s1",
            ),
        )
        assert result.output == "hi there"

        # TURN_END 后投影已生效：摘要 + 尾部原文
        projected = await history.get()
        assert projected[0].role == "system"
        assert projected[0].content == "summary text"
    finally:
        FakeProvider.set_default(None)
