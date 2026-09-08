"""Runner Agent 循环测试（mock LLM，不依赖真实 API）。"""

import pytest

from ..conf import ApprovalConfig, ApprovalDecision, ToolConfig
from ..core import Agent, HookVerdict, Runner, RunOptions, RunResult, SessionContext, ToolExecutor
from ..core.agent import ToolFilter
from ..schemas import UserMessage
from ..messages import InMemoryMessages
from ..infra import Tool

from .conftest import (
    FakeChannel,
    FakeCompletion,
    FakeMessage,
    FakeProvider,
    FakeToolCall,
    FakeUsage,
)


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
def allow_executor():
    return ToolExecutor(
        ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
    )


@pytest.fixture
def runner(allow_executor):
    """带放行审批执行器的 Runner（executor 由 Runner 持有）。"""
    return Runner(executor=allow_executor)


@pytest.fixture
def env(channel):
    """默认会话环境：历史（每测试隔离）+ 会话扩展 runner（工具表）。"""
    from ..core import EventBus, ExtensionRunner

    return SessionContext(
        channel=channel,
        history=InMemoryMessages(),
        ext_runner=ExtensionRunner(EventBus()),
    )


def make_echo_tool(result="echoed"):
    async def fn(args):
        return result

    return Tool(
        name="echo",
        description="echo back",
        parameters={"type": "object", "properties": {}},
        fn=fn,
    )


class TestRunnerSimple:
    """无工具调用的简单场景。"""

    async def test_single_turn_text_response(self, provider, env):
        provider.client.chat.completions.set_responses(
            FakeCompletion(FakeMessage(content="Hello!"))
        )
        agent = Agent(instruction="You are helpful.")
        runner = Runner()

        result = await runner.run(agent, "hi", env=env)

        assert result.output == "Hello!"
        assert result.completed_turns == 1
        assert result.input == "hi"

    async def test_instruction_in_messages(self, provider, env):
        provider.client.chat.completions.set_responses(
            FakeCompletion(FakeMessage(content="ok"))
        )
        agent = Agent(instruction="be concise")
        runner = Runner()

        await runner.run(agent, "hello", env=env)

        # _build_messages 返回会话历史 + system instruction
        messages = await runner._build_messages(agent, env.history, env)
        roles = [m.role for m in messages]
        assert "system" in roles

    async def test_none_content_output(self, provider, env):
        provider.client.chat.completions.set_responses(
            FakeCompletion(FakeMessage(content=None))
        )
        agent = Agent(instruction="helpful")
        runner = Runner()

        result = await runner.run(agent, "hi", env=env)
        assert result.output is None


class TestRunnerWithTools:
    """带工具调用的场景。"""

    async def test_tool_call_then_answer(self, provider, env, runner):
        provider.client.chat.completions.set_responses(
            FakeCompletion(
                FakeMessage(
                    tool_calls=[FakeToolCall(id="tc1", name="echo", arguments="{}")]
                )
            ),
            FakeCompletion(FakeMessage(content="I used echo")),
        )

        agent = Agent(instruction="helpful")
        env.ext_runner.register_tool(make_echo_tool("result!"))

        result = await runner.run(agent, "use echo", env=env)

        assert result.output == "I used echo"
        assert result.completed_turns == 2

    async def test_multiple_tool_calls_in_one_turn(self, provider, env, runner):
        async def fn_a(args):
            return "a"

        async def fn_b(args):
            return "b"

        tool_a = Tool(
            name="tool_a",
            description="a",
            parameters={"type": "object", "properties": {}},
            fn=fn_a,
        )
        tool_b = Tool(
            name="tool_b",
            description="b",
            parameters={"type": "object", "properties": {}},
            fn=fn_b,
        )

        provider.client.chat.completions.set_responses(
            FakeCompletion(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(id="t1", name="tool_a", arguments="{}"),
                        FakeToolCall(id="t2", name="tool_b", arguments="{}"),
                    ]
                )
            ),
            FakeCompletion(FakeMessage(content="done")),
        )

        agent = Agent(instruction="helpful")
        env.ext_runner.register_tool(tool_a, tool_b)

        result = await runner.run(agent, "use both", env=env)
        assert result.completed_turns == 2
        assert result.output == "done"

    async def test_unknown_tool_call(self, provider, env, runner):
        provider.client.chat.completions.set_responses(
            FakeCompletion(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(id="tc1", name="nonexistent", arguments="{}")
                    ]
                )
            ),
            FakeCompletion(FakeMessage(content="handled")),
        )

        agent = Agent(instruction="helpful")

        result = await runner.run(agent, "use unknown", env=env)
        assert result.completed_turns == 2
        assert result.output == "handled"


class TestRunnerMaxTurns:
    """max_turns 限制测试。"""

    async def test_max_turns_reached(self, provider, env, runner):
        responses = [
            FakeCompletion(
                FakeMessage(
                    tool_calls=[FakeToolCall(id=f"tc{i}", name="echo", arguments="{}")]
                )
            )
            for i in range(3)
        ]
        provider.client.chat.completions.set_responses(*responses)

        agent = Agent(instruction="helpful", tools=lambda: [make_echo_tool()])

        result = await runner.run(
            agent,
            "loop forever",
            env=env,
            options=RunOptions(max_turns=3),
        )
        assert result.completed_turns == 3
        assert result.output is None

    async def test_custom_max_turns(self, provider, env, runner):
        provider.client.chat.completions.set_responses(
            FakeCompletion(FakeMessage(content="immediate"))
        )
        agent = Agent(instruction="helpful")

        result = await runner.run(agent, "hi", env=env, options=RunOptions(max_turns=1))
        assert result.completed_turns == 1
        assert result.max_turns == 1


class TestRunnerHandoff:
    """Agent Handoff 测试。"""

    async def test_handoff_to_another_agent(self, provider, env, runner):
        provider.client.chat.completions.set_responses(
            FakeCompletion(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            id="tc1", name="transfer_to_researcher", arguments="{}"
                        )
                    ]
                )
            ),
            FakeCompletion(FakeMessage(content="researched!")),
        )

        researcher = Agent(
            name="researcher",
            description="research assistant",
            instruction="you research things",
        )
        main_agent = Agent(
            name="main",
            description="main agent",
            instruction="helpful",
            handoffs=["researcher"],
        )
        env.agents = {"main": main_agent, "researcher": researcher}

        result = await runner.run(
            main_agent,
            "research something",
            env=env,
        )
        assert result.completed_turns == 2
        assert result.last_agent.name == "researcher"
        assert result.output == "researched!"


class TestRunnerSubagent:
    """子 agent 派发（call_<name>）测试：隔离历史、返回结论、可继承工具。"""

    async def test_subagent_runs_isolated_and_returns(self, provider, env, runner):
        # 父：调 call_helper → 子跑一轮 → 父拿结论收尾
        provider.client.chat.completions.set_responses(
            FakeCompletion(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            id="tc1",
                            name="call_helper",
                            arguments='{"input": "compute 2+2"}',
                        )
                    ]
                )
            ),
            FakeCompletion(FakeMessage(content="child answer")),  # 子 agent 轮
            FakeCompletion(FakeMessage(content="final answer")),  # 父收尾轮
        )

        helper = Agent(
            name="helper",
            description="compute helper",
            instruction="you compute things",
        )
        main_agent = Agent(
            name="main",
            description="main agent",
            instruction="helpful",
            subagents=["helper"],
        )
        env.agents = {"main": main_agent, "helper": helper}

        result = await runner.run(main_agent, "do it", env=env)

        assert result.output == "final answer"
        # 子 agent 的结论作为工具结果回到父，父历史里能看到
        contents = [m.content for m in await env.history.get()]
        assert any("child answer" in str(c) for c in contents)

    async def test_subagent_inherits_ext_tools(self, provider, env, runner):
        """子 agent 继承父会话的工具源（否则派发出去无工具可用）。"""
        provider.client.chat.completions.set_responses(
            FakeCompletion(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            id="tc1", name="call_helper", arguments='{"input": "go"}'
                        )
                    ]
                )
            ),
            FakeCompletion(FakeMessage(content="child done")),  # 子 agent 轮
            FakeCompletion(FakeMessage(content="done")),  # 父收尾轮
        )
        env.ext_runner.register_tool(make_echo_tool("echoed-in-child"))

        helper = Agent(
            name="helper",
            description="helper",
            instruction="use tools",
            tool_filter=ToolFilter(allow=["echo"]),
        )
        main_agent = Agent(
            name="main",
            description="main agent",
            instruction="helpful",
            subagents=["helper"],
        )
        env.agents = {"main": main_agent, "helper": helper}

        await runner.run(main_agent, "do it", env=env)

        # 第 2 次 LLM 调用是子 agent 的轮次：工具表必须含继承来的 echo
        seen = provider.client.chat.completions.seen_tools
        assert "echo" in seen[1], f"子 agent 未继承工具源: {seen[1]}"

    async def test_subagent_env_inherits_capabilities(self, monkeypatch):
        """子环境继承 provider/cwd/ext_runner/agents，隔离历史与 channel，深度 +1。"""
        from ..core import runner as runner_mod

        captured: list[SessionContext] = []

        class SpyRunner:
            async def run(self, agent, input, env, options=None):
                captured.append(env)
                return RunResult(input=input, max_turns=1, output="ok")

        monkeypatch.setattr(runner_mod, "Runner", SpyRunner)

        helper = Agent(name="helper", description="helper")
        ext = runner_mod.ExtensionRunner(runner_mod.EventBus())
        parent = SessionContext(
            history=InMemoryMessages(),
            provider="fake-provider",
            channel="parent-channel",
            ext_runner=ext,
            cwd="/tmp/proj",
            agents={"helper": helper},
            depth=2,
        )

        tool = helper.as_tool(parent)
        assert await tool.run({"input": "go"}) == "ok"

        child = captured[0]
        assert child.provider == "fake-provider"
        assert child.ext_runner is ext
        assert child.cwd == "/tmp/proj"
        assert child.agents == {"helper": helper}
        assert child.depth == 3  # 父 depth + 1
        assert child.history is not parent.history  # 历史隔离
        assert child.channel is None  # headless：子 agent 不继承交互通道


class TestRunnerModelParams:
    """model_params 方法测试。"""

    def test_no_params(self, provider):
        agent = Agent(instruction="helpful")
        runner = Runner()
        assert runner.model_params(agent) == {}

    def test_temperature(self, provider):
        agent = Agent(instruction="helpful", temperature=0.7)
        runner = Runner()
        params = runner.model_params(agent)
        assert params["temperature"] == 0.7

    def test_output_type(self, provider):
        from pydantic import BaseModel

        class MyOutput(BaseModel):
            answer: str

        agent = Agent(instruction="helpful", output_type=MyOutput)
        runner = Runner()
        params = runner.model_params(agent)
        assert "response_format" in params
        assert params["response_format"]["type"] == "json_schema"


class TestRunnerBuildTools:
    """_build_tools 方法测试：源 = 会话工具列表（调用方从扩展 runner 取）。"""

    def _env(self, agents: dict | None = None, depth: int = 0) -> SessionContext:
        return SessionContext(
            history=InMemoryMessages(),
            agents=agents or {},
            depth=depth,
        )

    def test_includes_agent_tools(self, provider):
        tool = make_echo_tool()
        agent = Agent(
            instruction="helpful",
            tool_filter=ToolFilter(allow=["*"]),
        )
        runner = Runner()
        tools = runner._build_tools(agent, [tool], self._env())
        assert "echo" in tools

    def test_includes_handoff_tools(self, provider):
        researcher = Agent(name="researcher", description="research")
        agent = Agent(instruction="helpful", handoffs=["researcher"])
        runner = Runner()
        tools = runner._build_tools(
            agent, [], self._env({"researcher": researcher})
        )
        assert "transfer_to_researcher" in tools

    def test_handoff_missing_target_skipped(self, provider):
        """目标名不在注册表 → 跳过，不报错（配置与注册表解耦）。"""
        agent = Agent(instruction="helpful", handoffs=["ghost"])
        runner = Runner()
        assert runner._build_tools(agent, [], self._env()) == {}

    def test_includes_subagent_tool(self, provider):
        """subagents 按名解析为 call_<name> 派发工具。"""
        helper = Agent(name="helper", description="helper")
        agent = Agent(instruction="helpful", subagents=["helper"])
        runner = Runner()
        tools = runner._build_tools(agent, [], self._env({"helper": helper}))
        assert "call_helper" in tools

    def test_subagent_not_nested(self, provider):
        """depth>=1 不再挂派发工具，避免子 agent 无限套娃。"""
        helper = Agent(name="helper", description="helper")
        agent = Agent(instruction="helpful", subagents=["helper"])
        runner = Runner()
        tools = runner._build_tools(
            agent, [], self._env({"helper": helper}, depth=1)
        )
        assert "call_helper" not in tools

    def test_tool_filter_applied(self, provider):
        """agent.tool_filter 从会话工具源中筛选（allow/deny）。"""
        tool = make_echo_tool()
        agent = Agent(instruction="helpful", tool_filter=ToolFilter(allow=["echo"]))
        runner = Runner()
        assert "echo" in runner._build_tools(agent, [tool], self._env())
        agent2 = Agent(instruction="helpful", tool_filter=ToolFilter(allow=[]))
        assert runner._build_tools(agent2, [tool], self._env()) == {}

    def test_no_tools_returns_empty(self, provider):
        """空源（无 ext_runner / 无工具）→ 空工具。"""
        agent = Agent(instruction="helpful", tool_filter=ToolFilter(allow=["*"]))
        runner = Runner()
        assert runner._build_tools(agent, [], self._env()) == {}


class TestRunnerNoProvider:
    """无 Provider 时 run 应报错（默认 provider 缺失）。"""

    async def test_raises_without_provider(self):
        from ..infra import OpenAIProvider

        backup = OpenAIProvider._default
        OpenAIProvider._default = None
        try:
            agent = Agent(instruction="helpful")
            with pytest.raises(RuntimeError, match="no invalid provider"):
                await Runner().run(
                    agent,
                    "hi",
                    env=SessionContext(history=InMemoryMessages()),
                )
        finally:
            OpenAIProvider._default = backup


class TestRunnerToolHooks:
    """Runner 贯通 bus 到工具层（before/after 钩子）。"""

    async def test_tool_blocked_by_before_hook(self, provider, env):
        from ..core import EventBus
        from ..infra import ToolExecutionStartEvent

        async def block(evt):
            return HookVerdict(block="runner 层拦截")

        bus = EventBus()
        bus.on(ToolExecutionStartEvent, block)

        runner = Runner(
            executor=ToolExecutor(
                ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
            ),
            bus=bus,
        )

        provider.client.chat.completions.set_responses(
            FakeCompletion(
                FakeMessage(
                    tool_calls=[FakeToolCall(id="tc1", name="echo", arguments="{}")]
                )
            ),
            FakeCompletion(FakeMessage(content="done")),
        )

        agent = Agent(
            instruction="helpful",
            tools=lambda: [make_echo_tool()],
            tool_filter=ToolFilter(allow=["*"]),
        )
        result = await runner.run(agent, "use echo", env=env)

        # 工具被拦下 → 无输出结果，但流程继续（工具结果为空导致轮次结束）
        assert result.completed_turns == 2
        assert result.output == "done"

    async def test_tool_after_hook_notified(self, provider, env):
        from ..core import EventBus, ExtensionRunner
        from ..infra import ToolExecutionEndEvent

        seen: list[str] = []

        async def record(evt):
            seen.append(evt.tool_name)

        bus = EventBus()
        bus.on(ToolExecutionEndEvent, record)

        runner = Runner(
            executor=ToolExecutor(
                ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW))
            ),
            bus=bus,
        )

        provider.client.chat.completions.set_responses(
            FakeCompletion(
                FakeMessage(
                    tool_calls=[FakeToolCall(id="tc1", name="echo", arguments="{}")]
                )
            ),
            FakeCompletion(FakeMessage(content="done")),
        )

        agent = Agent(
            instruction="helpful",
            tool_filter=ToolFilter(allow=["*"]),
        )
        env.ext_runner = ExtensionRunner(bus)  # 工具经会话扩展 runner 提供
        env.ext_runner.register_tool(make_echo_tool())
        await runner.run(agent, "use echo", env=env)

        assert seen == ["echo"]
