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


async def test_activate_conflict_fails_whole_extension():
    bus = EventBus()
    registry = ToolRegistry()
    registry.add_tool(make_tool("dup"))  # 预置同名工具（builtin 已注册）
    mgr = ExtensionRegistry(bus, registry)

    async def duplicate(api: ExtensionAPI):
        api.register_tool(make_tool("dup"))  # 与预置名冲突 → 整体 FAILED
        api.register_tool(make_tool("ok"))  # 即便是唯一工具，也不生效
        api.on(Event.AGENT_START, lambda **kw: None)  # handler 不被挂载

    mgr.add("dup", duplicate)
    await mgr.load()
    await mgr.activate()

    ext = mgr.extensions[0]
    assert ext.state is ExtensionState.FAILED  # 冲突即抛错，不静默跳过
    assert ext.error  # 错误信息可读
    assert not bus.has(Event.AGENT_START)  # 未部分生效（handlers 未挂载）
    assert len(registry.all_tools()) == 1  # 仍是预置 dup，无新增 ok

    # 失败原因经 diagnostics 可见（ERROR 级）
    assert any(d.message == ext.error for d in mgr.diagnostics)


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
