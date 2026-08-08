"""Runner Agent 循环测试（mock LLM，不依赖真实 API）。"""
import pytest

from ..conf import ApprovalConfig, ApprovalDecision, ToolConfig
from ..core import Agent, Handoff, Runner, RunConfig, ToolExecutor
from ..schemas import UserMessage
from ..session.memory import InMemorySession
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

    async def test_single_turn_text_response(self, provider, channel):
        provider.client.chat.completions.set_responses(
            FakeCompletion(FakeMessage(content="Hello!"))
        )
        agent = Agent(instruction="You are helpful.")
        runner = Runner(channel=channel, provider=provider)

        result = await runner.run(agent, "hi")

        assert result.output == "Hello!"
        assert result.completed_turns == 1
        assert result.input == "hi"

    async def test_instruction_in_messages(self, provider, channel):
        provider.client.chat.completions.set_responses(
            FakeCompletion(FakeMessage(content="ok"))
        )
        agent = Agent(instruction="be concise")
        runner = Runner(channel=channel, provider=provider)

        await runner.run(agent, "hello")

        # _build_messages 返回 session 消息 + system instruction
        messages = await runner._build_messages(agent)
        roles = [m.role for m in messages]
        assert "system" in roles

    async def test_none_content_output(self, provider, channel):
        provider.client.chat.completions.set_responses(
            FakeCompletion(FakeMessage(content=None))
        )
        agent = Agent(instruction="helpful")
        runner = Runner(channel=channel, provider=provider)

        result = await runner.run(agent, "hi")
        assert result.output is None


class TestRunnerWithTools:
    """带工具调用的场景。"""

    async def test_tool_call_then_answer(self, provider, channel, allow_executor):
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
        runner = Runner(
            channel=channel,
            provider=provider,
            tool_executor=allow_executor,
            config=RunConfig(max_turns=5),
        )

        result = await runner.run(agent, "use echo")

        assert result.output == "I used echo"
        assert result.completed_turns == 2

    async def test_multiple_tool_calls_in_one_turn(self, provider, channel, allow_executor):
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
        runner = Runner(
            channel=channel,
            provider=provider,
            tool_executor=allow_executor,
        )

        result = await runner.run(agent, "use both")
        assert result.completed_turns == 2
        assert result.output == "done"

    async def test_unknown_tool_call(self, provider, channel, allow_executor):
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
        runner = Runner(
            channel=channel,
            provider=provider,
            tool_executor=allow_executor,
        )

        result = await runner.run(agent, "use unknown")
        assert result.completed_turns == 2
        assert result.output == "handled"


class TestRunnerMaxTurns:
    """max_turns 限制测试。"""

    async def test_max_turns_reached(self, provider, channel, allow_executor):
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
        runner = Runner(
            channel=channel,
            provider=provider,
            tool_executor=allow_executor,
            config=RunConfig(max_turns=3),
        )

        result = await runner.run(agent, "loop forever")
        assert result.completed_turns == 3
        assert result.output is None

    async def test_custom_max_turns(self, provider, channel, allow_executor):
        provider.client.chat.completions.set_responses(
            FakeCompletion(FakeMessage(content="immediate"))
        )
        agent = Agent(instruction="helpful")
        runner = Runner(
            channel=channel,
            provider=provider,
            tool_executor=allow_executor,
            config=RunConfig(max_turns=1),
        )

        result = await runner.run(agent, "hi")
        assert result.completed_turns == 1
        assert result.max_turns == 1


class TestRunnerHandoff:
    """Agent Handoff 测试。"""

    async def test_handoff_to_another_agent(self, provider, channel, allow_executor):
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

        runner = Runner(
            channel=channel,
            provider=provider,
            tool_executor=allow_executor,
        )

        result = await runner.run(main_agent, "research something")
        assert result.completed_turns == 2
        assert result.last_agent.name == "researcher"
        assert result.output == "researched!"


class TestRunnerModelParams:
    """model_params 方法测试。"""

    def test_no_params(self, provider):
        agent = Agent(instruction="helpful")
        runner = Runner(provider=provider)
        assert runner.model_params(agent) == {}

    def test_temperature(self, provider):
        agent = Agent(instruction="helpful", temperature=0.7)
        runner = Runner(provider=provider)
        params = runner.model_params(agent)
        assert params["temperature"] == 0.7

    def test_output_type(self, provider):
        from pydantic import BaseModel

        class MyOutput(BaseModel):
            answer: str

        agent = Agent(instruction="helpful", output_type=MyOutput)
        runner = Runner(provider=provider)
        params = runner.model_params(agent)
        assert "response_format" in params
        assert params["response_format"]["type"] == "json_schema"


class TestRunnerBuildTools:
    """_build_tools 方法测试。"""

    def test_includes_agent_tools(self, provider):
        tool = make_echo_tool()
        agent = Agent(instruction="helpful", tools=lambda: [tool])
        runner = Runner(provider=provider)
        tools = runner._build_tools(agent)
        assert "echo" in tools

    def test_includes_handoff_tools(self, provider):
        researcher = Agent(name="researcher", description="research")
        agent = Agent(
            instruction="helpful",
            handoffs=[Handoff(target=researcher)],
        )
        runner = Runner(provider=provider)
        tools = runner._build_tools(agent)
        assert "transfer_to_researcher" in tools

    def test_includes_tool_source(self, provider):
        """tools（工具源函数）返回的工具实时进入 Agent 工具列表。"""
        dynamic = make_echo_tool("dyn")

        agent = Agent(
            instruction="helpful",
            tools=lambda: [dynamic],
        )
        runner = Runner(provider=provider)
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
        runner = Runner(provider=provider)
        assert runner._build_tools(agent) == {}


class TestRunnerNoProvider:
    """无 Provider 时应报错。"""

    def test_raises_without_provider(self):
        OpenAIProvider_backup = None
        from ..infra.provider import OpenAIProvider
        OpenAIProvider_backup = OpenAIProvider._default
        OpenAIProvider._default = None
        try:
            with pytest.raises(RuntimeError, match="no invalid provider"):
                Runner()
        finally:
            OpenAIProvider._default = OpenAIProvider_backup


class TestRunnerKernel:
    """Runner 持有会话级 kernel：经 ToolContext 供 python 工具使用，close 回收。"""

    async def test_runner_owns_kernel_in_context(self, provider):
        """kernel 挂到 ToolContext，且同一 Runner 的 kernel 实例稳定（跨 run 保留状态）。"""
        runner = Runner(provider=provider)
        ctx = runner.tool_context()
        assert ctx.kernel is not None
        assert runner.tool_context().kernel is ctx.kernel   # 同一 kernel
        await runner.close()

    async def test_runner_kernel_executes_code(self, provider):
        """经 ToolContext 的 kernel 可直接执行代码。"""
        runner = Runner(provider=provider)
        ctx = runner.tool_context()
        assert await ctx.kernel.run("40 + 2") == "42"
        await runner.close()

    async def test_runner_kernel_state_persists_across_turns(self, provider):
        """同一 Runner 多次 run：kernel 状态跨 run 保留（会话级）。"""
        runner = Runner(provider=provider)
        ctx = runner.tool_context()
        assert await ctx.kernel.run("x = 10") == "(no output)"
        assert await ctx.kernel.run("x * 2") == "20"
        await runner.close()
