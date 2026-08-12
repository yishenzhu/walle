"""Runner Agent 循环测试（mock LLM，不依赖真实 API）。"""
import pytest

from ..conf import ApprovalConfig, ApprovalDecision, ToolConfig
from ..core import Agent, Handoff, Runner, RunOptions, SessionEnv, ToolExecutor
from ..schemas import UserMessage
from ..messages import InMemoryMessages
from ..tools import Tool

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
    return ToolExecutor(ToolConfig(approval=ApprovalConfig(default=ApprovalDecision.ALLOW)))


@pytest.fixture
def runner(allow_executor):
    """带放行审批执行器的 Runner（executor 由 Runner 持有）。"""
    return Runner(executor=allow_executor)


@pytest.fixture
def env(channel):
    """默认会话环境：独立 kernel + 历史（每测试隔离）。"""
    from ..infra import PyKernel

    return SessionEnv(
        channel=channel,
        kernel=PyKernel(),
        messages=InMemoryMessages(),
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
        messages = await runner._build_messages(agent, InMemoryMessages())
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
                    tool_calls=[
                        FakeToolCall(id="tc1", name="echo", arguments='{}')
                    ]
                )
            ),
            FakeCompletion(FakeMessage(content="I used echo")),
        )

        agent = Agent(instruction="helpful", tools=lambda: [make_echo_tool("result!")])

        result = await runner.run(
            agent, "use echo", env=env
        )

        assert result.output == "I used echo"
        assert result.completed_turns == 2

    async def test_multiple_tool_calls_in_one_turn(self, provider, env, runner):
        async def fn_a(args):
            return "a"

        async def fn_b(args):
            return "b"

        tool_a = Tool(name="tool_a", description="a", parameters={"type": "object", "properties": {}}, fn=fn_a)
        tool_b = Tool(name="tool_b", description="b", parameters={"type": "object", "properties": {}}, fn=fn_b)

        provider.client.chat.completions.set_responses(
            FakeCompletion(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(id="t1", name="tool_a", arguments='{}'),
                        FakeToolCall(id="t2", name="tool_b", arguments='{}'),
                    ]
                )
            ),
            FakeCompletion(FakeMessage(content="done")),
        )

        agent = Agent(instruction="helpful", tools=lambda: [tool_a, tool_b])

        result = await runner.run(
            agent, "use both", env=env
        )
        assert result.completed_turns == 2
        assert result.output == "done"

    async def test_unknown_tool_call(self, provider, env, runner):
        provider.client.chat.completions.set_responses(
            FakeCompletion(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(id="tc1", name="nonexistent", arguments='{}')
                    ]
                )
            ),
            FakeCompletion(FakeMessage(content="handled")),
        )

        agent = Agent(instruction="helpful")

        result = await runner.run(
            agent, "use unknown", env=env
        )
        assert result.completed_turns == 2
        assert result.output == "handled"


class TestRunnerMaxTurns:
    """max_turns 限制测试。"""

    async def test_max_turns_reached(self, provider, env, runner):
        responses = [
            FakeCompletion(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(id=f"tc{i}", name="echo", arguments='{}')
                    ]
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

        result = await runner.run(
            agent, "hi", env=env, options=RunOptions(max_turns=1)
        )
        assert result.completed_turns == 1
        assert result.max_turns == 1


class TestRunnerHandoff:
    """Agent Handoff 测试。"""

    async def test_handoff_to_another_agent(self, provider, env, runner):
        provider.client.chat.completions.set_responses(
            FakeCompletion(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(id="tc1", name="transfer_to_researcher", arguments='{}')
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
            handoffs=[Handoff(target=researcher)],
        )

        result = await runner.run(
            main_agent,
            "research something",
            env=env,
        )
        assert result.completed_turns == 2
        assert result.last_agent.name == "researcher"
        assert result.output == "researched!"


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
    """_build_tools 方法测试。"""

    def test_includes_agent_tools(self, provider):
        tool = make_echo_tool()
        agent = Agent(instruction="helpful", tools=lambda: [tool])
        runner = Runner()
        tools = runner._build_tools(agent)
        assert "echo" in tools

    def test_includes_handoff_tools(self, provider):
        researcher = Agent(name="researcher", description="research")
        agent = Agent(
            instruction="helpful",
            handoffs=[Handoff(target=researcher)],
        )
        runner = Runner()
        tools = runner._build_tools(agent)
        assert "transfer_to_researcher" in tools

    def test_includes_tool_source(self, provider):
        """tools（工具源函数）返回的工具实时进入 Agent 工具列表。"""
        dynamic = make_echo_tool("dyn")

        agent = Agent(
            instruction="helpful",
            tools=lambda: [dynamic],
        )
        runner = Runner()
        tools = runner._build_tools(agent)
        assert "echo" in tools

    def test_agent_tools_from_source(self):
        """agent.tools 源实时获取工具。"""
        dynamic = make_echo_tool("dyn")

        agent = Agent(
            instruction="helpful",
            tools=lambda: [dynamic],
        )
        names = {t.name for t in agent.tools()}
        assert names == {"echo"}

    def test_agent_tools_none(self, provider):
        """无 tools 源时 _build_tools 正常（空工具）。"""
        agent = Agent(instruction="helpful")
        runner = Runner()
        assert runner._build_tools(agent) == {}


class TestRunnerNoProvider:
    """无 Provider 时 run 应报错（默认 provider 缺失）。"""

    async def test_raises_without_provider(self):
        from ..infra import OpenAIProvider, PyKernel
        backup = OpenAIProvider._default
        OpenAIProvider._default = None
        try:
            agent = Agent(instruction="helpful")
            with pytest.raises(RuntimeError, match="no invalid provider"):
                await Runner().run(
                    agent, "hi", env=SessionEnv(kernel=PyKernel(), messages=InMemoryMessages())
                )
        finally:
            OpenAIProvider._default = backup


class TestRunnerKernel:
    """Runner 完全无状态：kernel 由调用方传入 run，工具经其执行。"""

    async def test_run_uses_passed_kernel(self, provider, channel, allow_executor):
        """run 传入的 kernel 被工具执行使用（同一 kernel 跨 run 状态保留）。"""
        from ..infra import PyKernel
        from ..tools import ToolContext, tool_context

        async def py(args):
            # 通过 tool_context 上下文变量拿 kernel 并执行
            ctx = tool_context.get()
            return await ctx.kernel.run("x = 5")

        tool = Tool(name="py", description="run py", parameters={"type": "object", "properties": {}}, fn=py)

        provider.client.chat.completions.set_responses(
            FakeCompletion(FakeMessage(
                tool_calls=[FakeToolCall(id="tc1", name="py", arguments="{}")]
            )),
            FakeCompletion(FakeMessage(content="done")),
        )
        agent = Agent(instruction="helpful", tools=lambda: [tool])

        runner = Runner(executor=allow_executor)
        kernel = PyKernel()
        result = await runner.run(
            agent,
            "run",
            env=SessionEnv(
                channel=channel,
                kernel=kernel,
                messages=InMemoryMessages(),
            ),
        )
        assert result.output == "done"
        await kernel.close()

    async def test_runner_kernel_state_persists_across_turns(self, provider, channel, allow_executor):
        """同一 kernel 跨多次 run 保留状态（会话级，由 Session 持有）。"""
        from ..infra import PyKernel
        from ..tools import tool_context

        async def py(args):
            ctx = tool_context.get()
            return await ctx.kernel.run(args["code"])

        tool = Tool(name="py", description="run py", parameters={"type": "object", "properties": {"code": {"type": "string"}}}, fn=py)

        provider.client.chat.completions.set_responses(
            FakeCompletion(FakeMessage(
                tool_calls=[FakeToolCall(id="t1", name="py", arguments='{"code": "y = 10"}')]
            )),
            FakeCompletion(FakeMessage(content="ok")),
            FakeCompletion(FakeMessage(
                tool_calls=[FakeToolCall(id="t2", name="py", arguments='{"code": "y * 2"}')]
            )),
            FakeCompletion(FakeMessage(content="done")),
        )
        agent = Agent(instruction="helpful", tools=lambda: [tool])

        runner = Runner(executor=allow_executor)
        kernel = PyKernel()
        r1 = await runner.run(
            agent, "set",
            env=SessionEnv(channel=channel, kernel=kernel, messages=InMemoryMessages()),
        )
        r2 = await runner.run(
            agent, "get",
            env=SessionEnv(channel=channel, kernel=kernel, messages=InMemoryMessages()),
        )
        assert r1.output == "ok"
        assert r2.output == "done"
        await kernel.close()
