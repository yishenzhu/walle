"""扩展系统测试：加载器(ExtensionRegistry)产出声明 + 激活器(ExtensionRunner)按会话生效。

覆盖：
1. load() 收集扩展声明（tools/handlers/commands）
2. factory 抛异常 → 该扩展声明 FAILED，其余正常加载
3. ExtensionRunner 把声明激活进会话：同名后到者覆盖
4. discover 目录发现 + 启停过滤
"""

import pytest

from ..core import (
    EventBus,
    ExtensionAPI,
    ExtensionRegistry,
    ExtensionRunner,
    ExtensionState,
    HookVerdict,
)
from ..infra import AgentStartEvent, ToolExecutionStartEvent, Tool


def make_tool(name: str) -> Tool:
    async def fn(args):
        return {"ok": name}

    return Tool(name=name, description=name, parameters={"type": "object"}, fn=fn)


async def test_load_produces_declarations():
    """load() 产出扩展声明（不激活、无副作用）。"""
    mgr = ExtensionRegistry()
    seen = []

    async def factory(api: ExtensionAPI):
        api.on(AgentStartEvent, lambda evt: seen.append(evt))
        api.register_tool(make_tool("ext_tool"))

    mgr.add("demo", factory)
    await mgr.load()
    assert len(mgr.extensions) == 1
    ext = mgr.extensions[0]
    assert ext.state is ExtensionState.LOADING  # 加载成功，未激活
    assert any(t.name == "ext_tool" for t in ext.tools)  # 声明里有工具
    assert AgentStartEvent in ext.handlers  # 声明里有事件订阅


async def test_load_and_activate_registers_handlers_and_tools():
    """声明经 ExtensionRunner 激活：工具/事件挂到会话 bus/工具表。"""
    bus = EventBus()
    runner = ExtensionRunner(bus)
    mgr = ExtensionRegistry()
    seen = []

    async def factory(api: ExtensionAPI):
        api.on(AgentStartEvent, lambda evt: seen.append(evt))
        api.register_tool(make_tool("ext_tool"))

    mgr.add("demo", factory)
    await mgr.load()

    runner.activate(mgr.extensions[0])
    assert bus.has(AgentStartEvent)  # handler 已挂载
    assert any(t.name == "ext_tool" for t in runner.all_tools())


async def test_failing_factory_is_isolated():
    mgr = ExtensionRegistry()

    async def bad(api: ExtensionAPI):
        raise RuntimeError("boom")

    async def good(api: ExtensionAPI):
        api.register_tool(make_tool("good_tool"))

    mgr.add("bad", bad)
    mgr.add("good", good)
    await mgr.load()

    assert mgr.extensions[0].state is ExtensionState.FAILED
    assert mgr.extensions[0].error == "boom"
    assert mgr.extensions[1].state is ExtensionState.LOADING  # 好的扩展声明可用


async def test_activate_duplicate_tool_later_overrides():
    """同一会话激活两个扩展注册同名工具：后激活者覆盖（后到者胜）。"""
    runner = ExtensionRunner(EventBus())
    mgr = ExtensionRegistry()
    second_tool = {"tool": None}

    async def first(api: ExtensionAPI):
        api.register_tool(make_tool("dup"))

    async def second(api: ExtensionAPI):
        tool = make_tool("dup")
        second_tool["tool"] = tool  # 记录实例供断言
        api.register_tool(tool)

    mgr.add("first", first)
    mgr.add("second", second)
    await mgr.load()

    runner.activate(mgr.extensions[0])  # first
    runner.activate(mgr.extensions[1])  # second → 覆盖

    assert runner.active_names == {"first", "second"}
    dups = [t for t in runner.all_tools() if t.name == "dup"]
    assert dups == [second_tool["tool"]]  # 只剩后到者实例


async def test_extension_tool_blocked_by_hook_end_to_end():
    """M1+M2+M4 全链路：扩展注册工具+钩子，经共享 bus，Runner 拦下工具执行。"""
    from ..conf import ApprovalConfig, ApprovalDecision, ToolConfig
    from ..messages import InMemoryMessages
    from ..core import Agent, Runner, SessionContext, ToolExecutor
    from ..core.agent import ToolFilter
    from .conftest import (
        FakeChannel,
        FakeCompletion,
        FakeMessage,
        FakeProvider,
        FakeToolCall,
    )

    provider = FakeProvider()
    FakeProvider.set_default(provider)
    try:
        bus = EventBus()
        blocked: dict = {}

        async def guard(evt):
            blocked["name"] = evt.tool_name
            return HookVerdict(block="guard 拦截")  # 拦下扩展工具

        bus.on(ToolExecutionStartEvent, guard)

        loader = ExtensionRegistry()
        ext_runner = ExtensionRunner(bus)

        async def ext(api: ExtensionAPI):
            api.register_tool(make_tool("guard_tool"))  # 扩展持有的工具

        loader.add("guard", ext)
        await loader.load()
        ext_runner.activate(loader.extensions[0])
        assert ext_runner.active_names == {"guard"}

        # 会话上下文携带扩展 runner：工具源与技能清单都从它来
        agent = Agent(
            instruction="helpful",
            tool_filter=ToolFilter(allow=["*"]),
        )

        runner = Runner(
            executor=ToolExecutor(
                ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
            ),
        )

        provider.client.chat.completions.set_responses(
            FakeCompletion(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(id="tc1", name="guard_tool", arguments="{}")
                    ]
                )
            ),
            FakeCompletion(FakeMessage(content="done")),
        )

        result = await runner.run(
            agent,
            "use guard",
            env=SessionContext(
                channel=FakeChannel(),
                history=InMemoryMessages(),
                ext_runner=ext_runner,
                bus=bus,
            ),
        )

        assert blocked["name"] == "guard_tool"  # before 钩子触发了
        assert result.output == "done"  # 工具被拦下后流程继续
    finally:
        FakeProvider.set_default(None)


# ── discover：目录扫描 + 入口契约 + 启停过滤 ──────────────


def _write_extension(root, name: str, body: str):
    """在 tmp 扩展目录里建 <name>/extension.py。"""
    d = root / name
    d.mkdir(parents=True)
    (d / "extension.py").write_text(body, encoding="utf-8")


EXT_ASYNC = """
from walle.core import ExtensionAPI
from walle.infra import Tool

async def load_extension(api: ExtensionAPI):
    api.register_tool(Tool(name="%s", description="%s",
                           parameters={"type": "object"}, fn=lambda a: "ok"))
""".strip()


async def test_discover_loads_extension_dir(tmp_path):
    """discover 扫到目录里的 extension.py，load 后产出声明。"""
    _write_extension(tmp_path, "alpha", EXT_ASYNC % ("alpha_tool", "alpha ext"))

    mgr = ExtensionRegistry()
    mgr.discover(root=tmp_path)

    assert [n for n, _ in mgr._factories] == ["alpha"]
    await mgr.load()
    assert len(mgr.extensions) == 1
    assert mgr.extensions[0].state is ExtensionState.LOADING
    assert any(t.name == "alpha_tool" for t in mgr.extensions[0].tools)


async def test_discover_enabled_whitelist(tmp_path):
    """enabled 非空时只加载名单内的扩展。"""
    _write_extension(tmp_path, "keep", EXT_ASYNC % ("k_tool", "keep"))
    _write_extension(tmp_path, "skip", EXT_ASYNC % ("s_tool", "skip"))

    mgr = ExtensionRegistry()
    mgr.discover(root=tmp_path, enabled=["keep"])

    await mgr.load()
    assert [e.name for e in mgr.extensions] == ["keep"]
    assert any(t.name == "k_tool" for t in mgr.extensions[0].tools)


async def test_discover_disabled_blacklist(tmp_path):
    """disabled 名单内的扩展不加载（即使 enabled 白名单包含它）。"""
    _write_extension(tmp_path, "keep", EXT_ASYNC % ("k_tool", "keep"))

    mgr = ExtensionRegistry()
    mgr.discover(root=tmp_path, enabled=["keep"], disabled=["keep"])

    await mgr.load()
    assert mgr.extensions == []


async def test_discover_missing_entry_fails_isolated(tmp_path):
    """缺入口文件的扩展在 load 阶段 FAILED，不影响其余。"""
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "extension.py").write_text("raise RuntimeError('boom')")
    _write_extension(tmp_path, "good", EXT_ASYNC % ("g_tool", "good"))

    mgr = ExtensionRegistry()
    mgr.discover(root=tmp_path)

    await mgr.load()
    names = {e.name: e.state for e in mgr.extensions}
    assert names["broken"] is ExtensionState.FAILED
    assert names["good"] is ExtensionState.LOADING  # 好的声明可用


# ── 内置工具走扩展注册（main.py 引导扩展同款组装）─────────


async def test_builtin_extensions_register_via_extension_system(tmp_path, monkeypatch):
    """内置工具作为引导扩展经 loader+runner 注册，落在会话工具表。"""
    from ..infra import Tool
    from ..tools import mcp as mcp_mod
    from ..tools.builtin import ask_user, bash, background, job_result, read

    # 隔离 skills / mcp 读取目录，避免真实 .agent 干扰
    monkeypatch.setattr(mcp_mod, "DOT_AGENT", tmp_path)

    async def builtin_ext(api: ExtensionAPI) -> None:  # main.py 同款
        for fn in (bash, ask_user, background, job_result, read):
            api.register_tool(Tool.from_function(fn))

    bus = EventBus()
    runner = ExtensionRunner(bus)
    mgr = ExtensionRegistry()
    mgr.add("builtin", builtin_ext)

    await mgr.load()
    runner.activate(*mgr.extensions)

    names = {t.name for t in runner.all_tools()}
    assert {"bash", "ask_user", "background", "job_result", "read"} <= names


# ── ExtensionRunner：会话级卸载 / 重激活 ─────────────────


async def test_unload_removes_tools_and_handlers():
    """卸载摘除扩展在会话中的挂载（工具/事件）。"""
    bus = EventBus()
    runner = ExtensionRunner(bus)
    mgr = ExtensionRegistry()
    seen = []

    async def ext(api: ExtensionAPI):
        api.register_tool(make_tool("ext_tool"))
        api.on(AgentStartEvent, lambda evt: seen.append(evt))

    mgr.add("ext", ext)
    await mgr.load()
    runner.activate(mgr.extensions[0])
    assert bus.has(AgentStartEvent)
    assert any(t.name == "ext_tool" for t in runner.all_tools())

    runner.unload("ext")
    assert not bus.has(AgentStartEvent)
    assert not any(t.name == "ext_tool" for t in runner.all_tools())
    assert runner.active_names == set()

    runner.unload("ext")  # 幂等：已卸载再卸不报错


async def test_reactivate_swaps_to_new_version():
    """同扩展换新声明重激活：先摘旧挂载再挂新的，无残留。"""
    bus = EventBus()
    runner = ExtensionRunner(bus)
    mgr = ExtensionRegistry()
    state = {"desc": "v1"}

    async def ext(api: ExtensionAPI):
        tool = make_tool("dyn")
        tool.description = f"dyn-{state['desc']}"
        api.register_tool(tool)
        api.on(AgentStartEvent, lambda evt: None)

    mgr.add("ext", ext)
    await mgr.load()
    runner.activate(mgr.extensions[0])
    first = next(t for t in runner.all_tools() if t.name == "dyn")
    assert first.description == "dyn-v1"
    assert bus.has(AgentStartEvent)

    # 换新版声明：重新 load（同 factory 名但产出不同）
    state["desc"] = "v2"
    mgr2 = ExtensionRegistry()
    mgr2.add("ext", ext)
    await mgr2.load()
    runner.activate(mgr2.extensions[0])  # 自动先卸载旧的再激活新的

    second = next(t for t in runner.all_tools() if t.name == "dyn")
    assert second.description == "dyn-v2"  # 新版生效
    assert len([t for t in runner.all_tools() if t.name == "dyn"]) == 1  # 无旧版残留


async def test_unload_covered_tool_keeps_later_owner():
    """A 的工具被 B 覆盖后，卸载 A 不误删 B 的工具实例。"""
    bus = EventBus()
    runner = ExtensionRunner(bus)
    mgr = ExtensionRegistry()
    b_tool = {"tool": None}

    async def a(api: ExtensionAPI):
        api.register_tool(make_tool("shared"))

    async def b(api: ExtensionAPI):
        tool = make_tool("shared")
        b_tool["tool"] = tool
        api.register_tool(tool)

    mgr.add("a", a)
    mgr.add("b", b)
    await mgr.load()

    runner.activate(mgr.extensions[0])  # a
    runner.activate(mgr.extensions[1])  # b → 覆盖 a 的 shared

    runner.unload("a")  # a 的工具已被 b 覆盖 → 不摘除

    shared = [t for t in runner.all_tools() if t.name == "shared"]
    assert shared == [b_tool["tool"]]  # b 的实例仍在


# ── 命令扩展点：register_command / dispatch / 卸载摘除 ─────


async def test_register_command_and_dispatch():
    """扩展注册斜杠命令：handler(args, ctx) 自决推送；未命中回退 agent。"""
    from ..infra import CommandContext
    from ..schemas import Delta, DeltaEnd
    from .conftest import FakeChannel

    bus = EventBus()
    runner = ExtensionRunner(bus)
    mgr = ExtensionRegistry()

    async def ext(api: ExtensionAPI):
        async def review(args: str, ctx_: CommandContext | None):
            # 推送由命令自己决定：经 channel 推 Delta 回复流
            await ctx_.channel.notify(Delta(delta=f"reviewing: {args or 'HEAD'}"))
            await ctx_.channel.notify(DeltaEnd())

        async def silent(args: str, ctx_: CommandContext | None):
            return None  # 命中但静默（不推送）

        api.register_command("review", "审查当前分支", review)
        api.register_command("silent", "静默命令", silent)

    mgr.add("cli", ext)
    await mgr.load()
    runner.activate(mgr.extensions[0])

    assert "review" in runner.commands
    ch = FakeChannel()
    ctx = CommandContext(channel=ch, bus=bus)
    assert await runner.dispatch("/review", ctx) is True
    assert [type(e).__name__ for e in ch.events] == ["Delta", "DeltaEnd"]
    assert ch.events[0].delta == "reviewing: HEAD"

    ch2 = FakeChannel()
    assert await runner.dispatch("/review main", CommandContext(ch2, bus)) is True
    assert ch2.events[0].delta == "reviewing: main"

    ch3 = FakeChannel()
    assert await runner.dispatch("/silent", CommandContext(ch3, bus)) is True  # 命中静默
    assert ch3.events == []

    assert await runner.dispatch("/nope", CommandContext(FakeChannel(), bus)) is False
    assert (
        await runner.dispatch("普通消息", CommandContext(FakeChannel(), bus)) is False
    )


async def test_unload_removes_command():
    """卸载扩展摘除其命令。"""
    from ..infra import CommandContext

    bus = EventBus()
    runner = ExtensionRunner(bus)
    mgr = ExtensionRegistry()

    async def ext(api: ExtensionAPI):
        async def h(args: str, ctx_: CommandContext | None):
            return None

        api.register_command("greet", "greet", h)

    mgr.add("ext", ext)
    await mgr.load()
    runner.activate(mgr.extensions[0])

    runner.unload("ext")
    assert runner.commands == {}
    ctx = CommandContext(channel=None, bus=bus)
    assert await runner.dispatch("/greet", ctx) is False


async def test_command_context_exposes_transport():
    """CommandContext 只暴露底层能力（transport/bus），用法由命令自决。"""
    from ..infra import CommandContext
    from ..schemas import Delta, DeltaEnd, Inquiry
    from .conftest import FakeChannel

    # 命令可经 channel notify 推送、call Inquiry 提问（无需预设接口）
    ch = FakeChannel()
    ch._inquiry_response = "user reply"
    ctx = CommandContext(channel=ch, bus=EventBus())
    await ctx.channel.notify(Delta(delta="自决推送"))
    await ctx.channel.notify(DeltaEnd())
    reply = await ctx.channel.call(Inquiry(question="自决提问", options=["a", "b"]))
    assert [type(e).__name__ for e in ch.events] == ["Delta", "DeltaEnd"]
    assert ch.events[0].delta == "自决推送"
    assert reply == "user reply"


# ── ExtensionRunner：会话级激活层 ────────────────────────


async def _load_extensions(mgr: ExtensionRegistry, factory) -> ExtensionRegistry:
    async def wrapper(api: ExtensionAPI):
        await factory(api)

    mgr.add("demo", wrapper)
    await mgr.load()
    return mgr


async def test_session_context_activates_into_own_bus_and_registry():
    """会话级激活：工具/事件挂到自己的 bus+registry，与其它会话隔离。"""
    from ..core import ExtensionRunner

    # 两个"会话"各自独立 bus + registry
    bus1 = EventBus()
    bus2 = EventBus()

    async def factory(api: ExtensionAPI):
        api.register_tool(make_tool("ext_tool"))
        api.on(AgentStartEvent, lambda evt: None)

    # 用 ExtensionRegistry 加载出声明
    loader = ExtensionRegistry()
    await _load_extensions(loader, factory)
    ext = loader.extensions[0]

    ctx1 = ExtensionRunner(bus1)
    ctx2 = ExtensionRunner(bus2)
    ctx1.activate(ext)
    ctx2.activate(ext)

    assert bus1.has(AgentStartEvent) and bus2.has(AgentStartEvent)
    assert any(t.name == "ext_tool" for t in ctx1.all_tools())
    assert any(t.name == "ext_tool" for t in ctx2.all_tools())

    # 卸载 ctx1 不影响 ctx2
    ctx1.unload("demo")
    assert not bus1.has(AgentStartEvent)
    assert bus2.has(AgentStartEvent)
    assert ctx1.all_tools() == []
    assert any(t.name == "ext_tool" for t in ctx2.all_tools())


async def test_session_context_command_is_per_session():
    """同一扩展在两个会话各激活一次：命令表互不干扰，卸载互不影响。"""
    from ..core import ExtensionRunner
    from ..infra import CommandContext
    from ..schemas import Delta, DeltaEnd
    from .conftest import FakeChannel

    async def factory(api: ExtensionAPI):
        async def greet(args: str, ctx_: CommandContext | None):
            await ctx_.channel.notify(Delta(delta=f"hi {args}"))
            await ctx_.channel.notify(DeltaEnd())

        api.register_command("greet", "greet", greet)

    loader = ExtensionRegistry()
    await _load_extensions(loader, factory)
    ext = loader.extensions[0]

    ch1, ch2 = FakeChannel(), FakeChannel()
    ctx1 = ExtensionRunner(EventBus())
    ctx2 = ExtensionRunner(EventBus())
    ctx1.activate(ext)
    ctx2.activate(ext)
    assert await ctx1.dispatch("/greet w", CommandContext(ch1, EventBus())) is True
    assert await ctx2.dispatch("/greet w", CommandContext(ch2, EventBus())) is True
    assert ch1.events[0].delta == "hi w"
    assert ch2.events[0].delta == "hi w"

    ctx1.unload("demo")
    assert (
        await ctx1.dispatch("/greet", CommandContext(FakeChannel(), EventBus()))
        is False
    )  # 已摘
    assert (
        await ctx2.dispatch("/greet w", CommandContext(FakeChannel(), EventBus()))
        is True
    )  # 仍在


async def test_session_context_reactivate_replaces():
    """同会话重新激活同一扩展：先摘旧挂载再挂新的，无重复。"""
    from ..core import ExtensionRunner

    async def factory(api: ExtensionAPI):
        api.register_tool(make_tool("t"))

    loader = ExtensionRegistry()
    await _load_extensions(loader, factory)
    ext = loader.extensions[0]

    ctx = ExtensionRunner(EventBus())
    ctx.activate(ext)
    ctx.activate(ext)  # 再次激活 → 先摘旧的

    tools = [t for t in ctx.all_tools() if t.name == "t"]
    assert len(tools) == 1  # 无重复残留
