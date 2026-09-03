"""ExtensionRegistry 两阶段加载测试。

覆盖：
1. load() 收集，activate() 挂 handlers / 注册 tools
2. factory 抛异常 → 该扩展 FAILED，其余正常激活
3. 激活冲突（工具重名）→ 整体 FAILED，不部分生效
"""

import pytest

from ..core import (
    Event,
    EventBus,
    ExtensionAPI,
    ExtensionRegistry,
    ExtensionState,
    HookVerdict,
)
from ..tools import Tool, ToolRegistry


def make_tool(name: str) -> Tool:
    async def fn(args):
        return {"ok": name}

    return Tool(name=name, description=name, parameters={"type": "object"}, fn=fn)


async def test_load_then_activate_registers_handlers_and_tools():
    bus = EventBus()
    registry = ToolRegistry()
    mgr = ExtensionRegistry(bus, registry)
    seen = []

    async def factory(api: ExtensionAPI):
        api.on(Event.AGENT_START, lambda **kw: seen.append(kw))
        api.register_tool(make_tool("ext_tool"))

    mgr.add("demo", factory)
    await mgr.load()
    assert len(mgr.extensions) == 1
    assert mgr.extensions[0].state is ExtensionState.LOADING  # 未激活

    await mgr.activate()
    ext = mgr.active[0]
    assert ext.state is ExtensionState.ACTIVE
    assert bus.has(Event.AGENT_START)  # handler 已挂载
    assert any(t.name == "ext_tool" for t in registry.all_tools())


async def test_failing_factory_is_isolated():
    bus = EventBus()
    registry = ToolRegistry()
    mgr = ExtensionRegistry(bus, registry)

    async def bad(api: ExtensionAPI):
        raise RuntimeError("boom")

    async def good(api: ExtensionAPI):
        api.register_tool(make_tool("good_tool"))

    mgr.add("bad", bad)
    mgr.add("good", good)
    await mgr.load()
    await mgr.activate()

    assert mgr.extensions[0].state is ExtensionState.FAILED
    assert mgr.extensions[0].error == "boom"
    assert mgr.extensions[1].state is ExtensionState.ACTIVE
    assert any(t.name == "good_tool" for t in registry.all_tools())


async def test_activate_duplicate_tool_later_overrides():
    """扩展与扩展同名工具：后激活者覆盖先激活者（后到者胜，均 ACTIVE）。"""
    bus = EventBus()
    registry = ToolRegistry()
    mgr = ExtensionRegistry(bus, registry)
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
    await mgr.activate()

    assert {e.name for e in mgr.active} == {"first", "second"}
    dups = [t for t in registry.all_tools() if t.name == "dup"]
    assert dups == [second_tool["tool"]]  # 只剩后到者实例


async def test_extension_tool_blocked_by_hook_end_to_end():
    """M1+M2+M4 全链路：扩展注册工具+钩子，经共享 bus，Runner 拦下工具执行。"""
    from ..conf import ApprovalConfig, ApprovalDecision, ToolConfig
    from ..messages import InMemoryMessages
    from ..core import Agent, Runner, SessionEnv, ToolExecutor
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
        registry = ToolRegistry()
        blocked: dict = {}

        async def guard(**ctx_):
            blocked["name"] = ctx_["tool_name"]
            return HookVerdict(block="guard 拦截")  # 拦下扩展工具

        bus.on(Event.TOOL_EXECUTION_START, guard)

        mgr = ExtensionRegistry(bus, registry)

        async def ext(api: ExtensionAPI):
            api.register_tool(make_tool("guard_tool"))  # 扩展持有的工具

        mgr.add("guard", ext)
        await mgr.load()
        await mgr.activate()
        assert any(
            e.name == "guard" and e.state is ExtensionState.ACTIVE for e in mgr.active
        )

        # 用 registry 的全部工具构造 agent（main.py 闭包同款）
        def toolkit():
            return registry.all_tools()

        agent = Agent(
            instruction="helpful",
            tools=toolkit,
            tool_filter=ToolFilter(allow=["*"]),
        )

        runner = Runner(
            executor=ToolExecutor(
                ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
            ),
            bus=bus,
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
            env=SessionEnv(channel=FakeChannel(), messages=InMemoryMessages()),
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
from walle.tools import Tool

async def load_extension(api: ExtensionAPI):
    api.register_tool(Tool(name="%s", description="%s",
                           parameters={"type": "object"}, fn=lambda a: "ok"))
""".strip()


async def test_discover_loads_extension_dir(tmp_path):
    """discover 扫到目录里的 extension.py，load+activate 后工具生效。"""
    _write_extension(tmp_path, "alpha", EXT_ASYNC % ("alpha_tool", "alpha ext"))

    bus = EventBus()
    registry = ToolRegistry()
    mgr = ExtensionRegistry(bus, registry)
    mgr.discover(root=tmp_path)

    assert [n for n, _ in mgr._factories] == ["alpha"]
    await mgr.load()
    await mgr.activate()
    assert any(e.name == "alpha" and e.state is ExtensionState.ACTIVE for e in mgr.active)
    assert any(t.name == "alpha_tool" for t in registry.all_tools())


async def test_discover_enabled_whitelist(tmp_path):
    """enabled 非空时只加载名单内的扩展。"""
    _write_extension(tmp_path, "keep", EXT_ASYNC % ("k_tool", "keep"))
    _write_extension(tmp_path, "skip", EXT_ASYNC % ("s_tool", "skip"))

    bus = EventBus()
    registry = ToolRegistry()
    mgr = ExtensionRegistry(bus, registry)
    mgr.discover(root=tmp_path, enabled=["keep"])

    await mgr.load()
    await mgr.activate()
    assert [e.name for e in mgr.active] == ["keep"]
    assert any(t.name == "k_tool" for t in registry.all_tools())


async def test_discover_disabled_blacklist(tmp_path):
    """disabled 名单内的扩展不加载（即使 enabled 白名单包含它）。"""
    _write_extension(tmp_path, "keep", EXT_ASYNC % ("k_tool", "keep"))

    bus = EventBus()
    registry = ToolRegistry()
    mgr = ExtensionRegistry(bus, registry)
    mgr.discover(root=tmp_path, enabled=["keep"], disabled=["keep"])

    await mgr.load()
    await mgr.activate()
    assert mgr.active == []


async def test_discover_missing_entry_fails_isolated(tmp_path):
    """缺入口文件的扩展在 load 阶段 FAILED，不影响其余。"""
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "extension.py").write_text("raise RuntimeError('boom')")
    _write_extension(tmp_path, "good", EXT_ASYNC % ("g_tool", "good"))

    bus = EventBus()
    registry = ToolRegistry()
    mgr = ExtensionRegistry(bus, registry)
    mgr.discover(root=tmp_path)

    await mgr.load()
    await mgr.activate()
    names = {e.name: e.state for e in mgr.extensions}
    assert names["broken"] is ExtensionState.FAILED
    assert names["good"] is ExtensionState.ACTIVE


# ── 内置工具走扩展注册（main.py 引导扩展同款组装）─────────


async def test_builtin_extensions_register_via_extension_system(tmp_path, monkeypatch):
    """内置工具作为引导扩展经 ExtensionRegistry 注册，落在纯容器里。"""
    from ..tools import Tool
    from ..tools import mcp as mcp_mod
    from ..tools.builtin import ask_user, bash, background, job_result, read

    # 隔离 skills / mcp 读取目录，避免真实 .agent 干扰
    monkeypatch.setattr(mcp_mod, "DOT_AGENT", tmp_path)

    async def builtin_ext(api: ExtensionAPI) -> None:  # main.py 同款
        for fn in (bash, ask_user, background, job_result, read):
            api.register_tool(Tool.from_function(fn))

    bus = EventBus()
    registry = ToolRegistry()
    mgr = ExtensionRegistry(bus, registry)
    mgr.add("builtin", builtin_ext)

    await mgr.load()
    await mgr.activate()

    names = {t.name for t in registry.all_tools()}
    assert {"bash", "ask_user", "background", "job_result", "read"} <= names
    assert mgr.extensions[0].state is ExtensionState.ACTIVE


# ── unload / reload：生命周期管理 ────────────────────────


async def test_unload_removes_tools_and_handlers():
    """卸载摘除扩展挂载的工具与事件监听器，状态置 UNLOADED。"""
    bus = EventBus()
    registry = ToolRegistry()
    mgr = ExtensionRegistry(bus, registry)
    seen = []

    async def ext(api: ExtensionAPI):
        api.register_tool(make_tool("ext_tool"))
        api.on(Event.AGENT_START, lambda **kw: seen.append(kw))

    mgr.add("ext", ext)
    await mgr.load()
    await mgr.activate()
    assert bus.has(Event.AGENT_START)
    assert any(t.name == "ext_tool" for t in registry.all_tools())

    mgr.unload("ext")
    assert not bus.has(Event.AGENT_START)
    assert not any(t.name == "ext_tool" for t in registry.all_tools())
    assert mgr.extensions[0].state is ExtensionState.UNLOADED

    mgr.unload("ext")  # 幂等：已卸载再卸不报错


async def test_reload_swaps_to_new_version():
    """reload 重跑 factory：新版工具与 handler 生效，旧挂载先摘除。"""
    bus = EventBus()
    registry = ToolRegistry()
    mgr = ExtensionRegistry(bus, registry)
    state = {"desc": "v1"}

    async def ext(api: ExtensionAPI):
        tool = make_tool("dyn")
        tool.description = f"dyn-{state['desc']}"
        api.register_tool(tool)
        api.on(Event.AGENT_START, lambda **kw: None)

    mgr.add("ext", ext)
    await mgr.load()
    await mgr.activate()
    first = next(t for t in registry.all_tools() if t.name == "dyn")
    assert first.description == "dyn-v1"
    assert bus.has(Event.AGENT_START)

    state["desc"] = "v2"
    await mgr.reload("ext")

    second = next(t for t in registry.all_tools() if t.name == "dyn")
    assert second.description == "dyn-v2"  # 新版生效
    assert mgr.extensions[0].state is ExtensionState.ACTIVE
    assert len([t for t in registry.all_tools() if t.name == "dyn"]) == 1  # 无旧版残留


async def test_unload_covered_tool_keeps_later_owner():
    """A 的工具被 B 覆盖后，卸载 A 不误删 B 的工具实例。"""
    bus = EventBus()
    registry = ToolRegistry()
    mgr = ExtensionRegistry(bus, registry)
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
    await mgr.activate()

    mgr.unload("a")  # a 的工具已被 b 覆盖 → 不摘除

    shared = [t for t in registry.all_tools() if t.name == "shared"]
    assert shared == [b_tool["tool"]]  # b 的实例仍在


# ── 命令扩展点：register_command / dispatch / 卸载摘除 ─────


async def test_register_command_and_dispatch():
    """扩展注册斜杠命令，dispatch 命中返回回复、未命中回退 None。"""
    bus = EventBus()
    registry = ToolRegistry()
    mgr = ExtensionRegistry(bus, registry)

    async def ext(api: ExtensionAPI):
        async def review(args: str) -> str:
            return f"reviewing: {args or 'HEAD'}"

        api.register_command("review", "审查当前分支", review)

    mgr.add("cli", ext)
    await mgr.load()
    await mgr.activate()

    assert "review" in mgr.commands
    assert await mgr.dispatch("/review") == "reviewing: HEAD"
    assert await mgr.dispatch("/review main") == "reviewing: main"
    assert await mgr.dispatch("/nope") is None  # 未知命令回退 agent
    assert await mgr.dispatch("普通消息") is None  # 非 / 开头回退 agent


async def test_unload_removes_command():
    """卸载扩展摘除其命令；被覆盖的命令不误删覆盖者。"""
    bus = EventBus()
    registry = ToolRegistry()
    mgr = ExtensionRegistry(bus, registry)

    async def ext(api: ExtensionAPI):
        async def h(args: str) -> str:
            return "v1"

        api.register_command("greet", "greet", h)

    mgr.add("ext", ext)
    await mgr.load()
    await mgr.activate()

    mgr.unload("ext")
    assert mgr.commands == {}
    assert await mgr.dispatch("/greet") is None
