"""插件运行时测试：依赖驱动激活 + 作用域 + 工具落表 + 失败隔离。"""

import pytest

from ..channel.web.plugins import discover_web_plugins
from ..core import HookVerdict, Tools
from ..infra import AgentStartEvent, PreExecuteToolEvent, Tool
from ..plugin import Plugin, PluginHost, discover_packages, plugin
from ..spec import ToolService


def make_tool(name: str) -> Tool:
    async def fn(args):
        return {"ok": name}

    return Tool(name=name, description=name, parameters={"type": "object"}, fn=fn)


def tool_plugin(name: str, tool_name: str) -> Plugin:
    """声明一个只往 ToolService 注册一个工具的插件。"""

    @plugin(name=name, inject=(ToolService,))
    async def apply(ctx, config) -> None:
        ctx.get(ToolService).add(make_tool(tool_name))

    return apply


async def test_launch_activates_and_registers():
    """依赖齐全即激活：工具落表、事件挂到共享总线。"""
    host = PluginHost()
    table = Tools()
    await host.root.register(ToolService, table)
    seen = []

    @plugin(name="demo", inject=(ToolService,))
    async def demo(ctx, config) -> None:
        ctx.get(ToolService).add(make_tool("plugin_tool"))
        ctx.on(AgentStartEvent, lambda evt: seen.append(evt))

    try:
        entries = await host.launch([demo])
        assert entries[0].state.value == "active"
        assert table.has("plugin_tool")
        assert host.root.bus.has(AgentStartEvent)
    finally:
        await host.dispose()


async def test_later_plugin_overrides_same_tool():
    """同名工具后激活者覆盖先到者，不留旧实例。"""
    host = PluginHost()
    table = Tools()
    await host.root.register(ToolService, table)
    second: dict = {}

    @plugin(name="second", inject=(ToolService,))
    async def second_apply(ctx, config) -> None:
        tool = make_tool("dup")
        second["tool"] = tool
        ctx.get(ToolService).add(tool)

    try:
        await host.launch([tool_plugin("first", "dup"), second_apply])
        assert [t for t in table.all_tools() if t.name == "dup"] == [second["tool"]]
    finally:
        await host.dispose()


async def test_pending_until_dependency_registered():
    """依赖未就绪保持 PENDING；能力注册后自动激活。"""
    host = PluginHost()
    entries = await host.launch([tool_plugin("needs-tools", "t")])
    assert entries[0].state.value == "pending"

    table = Tools()
    await host.root.register(ToolService, table)
    assert entries[0].state.value == "active"
    assert table.has("t")


async def test_failing_plugin_is_isolated():
    """单个插件 apply 抛错 → FAILED，不影响其余插件激活。"""
    host = PluginHost()

    @plugin(name="bad")
    async def bad(ctx, config) -> None:
        raise RuntimeError("boom")

    @plugin(name="good")
    async def good(ctx, config) -> None:
        return None

    entries = await host.launch([bad, good])
    assert entries[0].state.value == "failed"
    assert entries[0].error == "boom"
    assert entries[1].state.value == "active"


async def test_duplicate_provider_rejected():
    """同一能力被两个插件声明提供 → 启动即报错。"""

    @plugin(name="a", provides=(ToolService,))
    async def a(ctx, config) -> None:
        return None

    @plugin(name="b", provides=(ToolService,))
    async def b(ctx, config) -> None:
        return None

    with pytest.raises(ValueError):
        await PluginHost().launch([a, b])


async def test_stop_rolls_back_subscriptions():
    """stop 逆序回滚 effect：插件订阅的事件被摘除。"""
    host = PluginHost()
    await host.root.register(ToolService, Tools())

    @plugin(name="demo", inject=(ToolService,))
    async def demo(ctx, config) -> None:
        ctx.on(AgentStartEvent, lambda evt: None)

    await host.launch([demo])
    assert host.root.bus.has(AgentStartEvent)
    await host.stop()
    assert not host.root.bus.has(AgentStartEvent)


async def test_stop_reclaims_plugin_tools():
    """插件卸载回收它注册的工具。"""
    host = PluginHost()
    table = Tools()
    await host.root.register(ToolService, table)
    await host.launch([tool_plugin("demo", "t")])
    assert table.has("t")
    await host.stop()
    assert not table.has("t")


async def test_stop_reclaims_plugin_skills():
    """插件卸载回收它注册的技能。"""
    from ..infra import Skills
    from ..spec import SkillService

    host = PluginHost()
    table = Skills()
    await host.root.register(SkillService, table)

    @plugin(name="skills", inject=(SkillService,))
    async def skills(ctx, config) -> None:
        ctx.get(SkillService).add("grill", "Grill the user", "/s/SKILL.md")

    await host.launch([skills])
    assert [s.name for s in table.list()] == ["grill"]
    await host.stop()
    assert table.list() == []


async def test_builtin_plugin_registers_tools():
    """内置工具插件经统一插件系统注册进会话工具表。"""
    from ..tools.builtin.plugin import builtin_plugin

    host = PluginHost()
    table = Tools()
    await host.root.register(ToolService, table)
    try:
        await host.launch([builtin_plugin])
        names = {t.name for t in table.all_tools()}
        assert {"bash", "read", "write"} <= names
    finally:
        await host.dispose()


async def test_plugin_tool_blocked_by_hook_end_to_end():
    """插件注册工具 + 钩子经共享总线拦下工具执行，流程继续。"""
    from ..conf import ApprovalConfig, ApprovalDecision, ToolConfig
    from ..core import Agent, Runner, SessionContext, ToolExecutor
    from ..core.agent import ToolFilter
    from ..messages import InMemoryMessages
    from .conftest import (
        FakeChannel,
        FakeCompletion,
        FakeMessage,
        FakeProvider,
        FakeToolCall,
    )

    provider = FakeProvider()
    host = PluginHost()
    bus = host.root.bus
    table = Tools()
    await host.root.register(ToolService, table)
    blocked: dict = {}

    async def guard(evt):
        blocked["name"] = evt.name
        return HookVerdict(block="guard 拦截")

    bus.on(PreExecuteToolEvent, guard)
    await host.launch([tool_plugin("guard", "guard_tool")])

    agent = Agent(instruction="helpful", tool_filter=ToolFilter(allow=["*"]))
    runner = Runner(
        executor=ToolExecutor(
            ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
        ),
    )
    provider.client.chat.completions.set_responses(
        FakeCompletion(
            FakeMessage(
                tool_calls=[FakeToolCall(id="tc1", name="guard_tool", arguments="{}")]
            )
        ),
        FakeCompletion(FakeMessage(content="done")),
    )

    result = await runner.run(
        agent,
        "use guard",
        env=SessionContext(
            provider=provider,
            channel=FakeChannel(),
            history=InMemoryMessages(),
            tools=table,
            bus=bus,
        ),
    )

    assert blocked["name"] == "guard_tool"
    assert result.output == "done"
    await host.dispose()


async def test_discover_packages_and_web_plugins():
    """包内 plugin.py 声明 agent 插件；WEB_PLUGINS 声明 Web 插件。"""
    agent_plugins = {plugin.name: plugin for plugin in discover_packages()}
    assert "learning" in agent_plugins

    web_plugins = {plugin.name: plugin for plugin in discover_web_plugins()}
    assert "learning-web" in web_plugins
