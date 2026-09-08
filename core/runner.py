import asyncio
import logging
from dataclasses import dataclass, field
from pydantic import BaseModel
from typing import Any

from .agent import Agent, Handoff
from .executor import ToolExecutor
from ..channel import Channel
from ..messages import Messages, InMemoryMessages
from ..infra import (
    EventBus,
    ExtensionRunner,
    Job,
    OpenAIProvider,
    Tool,
    ToolContext,
    tool_context,
    tracer,
    AgentStartEvent,
    AgentEndEvent,
    SessionStartEvent,
    SessionEndEvent,
    TurnStartEvent,
    TurnEndEvent,
    MessageStartEvent,
    MessageDeltaEvent,
    MessageEndEvent,
    AGENT_ITERATIONS,
    HANDOFF,
)
from ..schemas import (
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    Usage,
    UserMessage,
    ToolResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_TURNS = 10


@dataclass
class RunOptions:
    """单次 run 的可选行为配置（怎么做），与会话上下文（SessionContext）分离。"""

    max_turns: int = DEFAULT_MAX_TURNS
    streamed: bool = False  # 流式输出（delta 通知）


@dataclass
class SessionContext:
    """会话级上下文与状态：Session 唯一持有，每次 run 原样传入。

    携带会话的扩展激活层（ext_runner）：工具执行期经它动态注册新工具。
    """

    messages: Messages  # 会话历史（必填）
    provider: OpenAIProvider = None  # 模型接入（None 用 Runner 默认）
    channel: Channel = None  # 会话 channel 端点（仅交互，不承担会话身份）
    session_id: str | None = None  # 会话身份（内聚在 context，而非 channel）
    jobs: dict[str, Job] = field(default_factory=dict)  # 后台作业表（跨轮存活）
    ext_runner: ExtensionRunner | None = None  # 会话扩展激活层（工具可动态注册）
    cwd: str | None = None  # 会话工作目录（无则不设，工具继承进程 cwd）


class RunResult(BaseModel):
    input: str
    last_agent: Agent | None = None
    max_turns: int
    completed_turns: int = 0
    output: str | BaseModel | None = None

    model_config = {"arbitrary_types_allowed": True}


class Runner:
    """Agent 执行器：持有工具执行器 / 事件总线；provider 由 run 时 env 提供。"""

    def __init__(
        self,
        executor: ToolExecutor | None = None,
        bus: EventBus | None = None,
    ) -> None:
        # provider 不经构造——run 时由 env 提供（env 缺省回退默认实例）
        self._executor = executor or ToolExecutor()
        self._bus = bus or EventBus()

    async def run(
        self,
        agent: Agent,
        input: str,
        env: SessionContext,
        options: RunOptions | None = None,
    ) -> RunResult:
        options = options or RunOptions()
        provider = env.provider or OpenAIProvider.get_default()
        if provider is None:
            raise RuntimeError("no invalid provider")
        channel, history = env.channel, env.messages
        streamed = options.streamed
        session_id = env.session_id
        # run = 一次会话交互：session 边界 + 本条用户消息边界
        await self._bus.emit(SessionStartEvent(session_id=session_id))
        await self._bus.emit(
            MessageStartEvent(input=input, session_id=session_id)
        )
        await history.add([UserMessage(content=input)])

        await self._bus.emit(AgentStartEvent(agent=agent.name, session_id=session_id))

        with tracer.start_as_current_span("agent.run") as span:
            span.set_attribute("streamed", streamed)
            turn = 0
            while turn < options.max_turns:
                turn += 1
                span.set_attribute("agent.turn", turn)
                model = provider.model
                span.set_attribute("agent.model", model)

                messages = await self._build_messages(agent, history, env)
                tool_source = (
                    env.ext_runner.all_tools() if env.ext_runner is not None else []
                )
                tools = self._build_tools(agent, tool_source)

                await self._bus.emit(TurnStartEvent(turn=turn, agent=agent.name))

                # 本轮执行上下文：分支前统一拼接（两处 _run_turn* 共用），
                # 并统一注入 tool_context——整轮工具（含并发、审批扩展 /
                # preflight 钩子）经它拿会话上下文，executor 不再每工具设置。
                ctx = ToolContext(
                    channel=channel,
                    jobs=env.jobs,
                    bus=self._bus,
                    cwd=env.cwd,
                    ext=env.ext_runner,
                    history=history,
                )
                tool_context.set(ctx)
                if streamed:
                    completion, message, tool_results = await self._run_turn_streamed(
                        agent, messages, tools, provider
                    )
                else:
                    completion, message, tool_results = await self._run_turn(
                        agent, messages, tools, provider
                    )
                # 本轮工具执行完：拉起 background 写下的 pending 作业（后台异步跑）
                await self._executor.launch_pending(tools)

                usage = Usage.model_validate(completion.usage)
                message = AssistantMessage.from_response(message)
                await history.add([message], usage=usage)
                await history.add(
                    [
                        ToolMessage(content=str(r), tool_call_id=tc_id)
                        for tc_id, r in tool_results
                    ]
                )

                handoff = next(
                    (r for _, r in tool_results if isinstance(r, Handoff)), None
                )

                if handoff is not None:
                    target = handoff.target
                    target_name = target.name or "unknown"
                    logger.info(f"handoff: {agent.name} -> {target_name}")
                    span.add_event("agent.handoff", {"target": target_name})
                    HANDOFF.add(1, {"from": agent.name or "", "to": target_name})
                    agent = target

                if len(tool_results) == 0:
                    AGENT_ITERATIONS.record(turn)
                    span.set_attribute("agent.iterations", turn)
                    output = self._format_output(agent, message.content)
                    await self._bus.emit(
                        TurnEndEvent(
                            turn=turn,
                            agent=agent.name,
                            session_id=session_id,
                            history=history,
                            usage=usage,
                            provider=provider,
                        )
                    )
                    await self._bus.emit(
                        MessageEndEvent(output=output, session_id=session_id)
                    )
                    await self._bus.emit(AgentEndEvent(agent=agent.name))
                    await self._bus.emit(SessionEndEvent(session_id=session_id))
                    return RunResult(
                        input=input,
                        last_agent=agent,
                        output=output,
                        max_turns=options.max_turns,
                        completed_turns=turn,
                    )

                await self._bus.emit(
                    TurnEndEvent(
                        turn=turn,
                        agent=agent.name,
                        session_id=session_id,
                        history=history,
                        usage=usage,
                        provider=provider,
                    )
                )

            AGENT_ITERATIONS.record(turn)
            span.set_attribute("agent.iterations", turn)
            logger.warning(f"max turns ({options.max_turns}) reached. Stopping.")
            await self._bus.emit(MessageEndEvent(output=None, session_id=session_id))
            await self._bus.emit(AgentEndEvent(agent=agent.name))
            await self._bus.emit(SessionEndEvent(session_id=session_id))
            return RunResult(
                input=input,
                last_agent=agent,
                max_turns=options.max_turns,
                completed_turns=turn,
            )

    async def _run_turn_streamed(
        self,
        agent: Agent,
        messages: list,
        tools: dict[str, Tool],
        provider,
    ):
        # 流式增量只发事件（MESSAGE_DELTA）——推给 channel 由监听者负责
        tool_results: list = []
        async with provider.stream(
            messages=[m.model_dump() for m in messages],  # type: ignore
            tools=[t.formatted_schema() for t in tools.values()],  # type: ignore
            **self.model_params(agent),
        ) as stream:
            async for event in stream:
                if event.type == "content.delta":
                    await self._bus.emit(MessageDeltaEvent(delta=event.delta))

            completion = await stream.get_final_completion()
            message = completion.choices[0].message
            if message.tool_calls:
                async for tc_id, r in self._executor.execute_calls(
                    message.tool_calls, tools
                ):
                    tool_results.append((tc_id, r))
        return completion, message, tool_results

    async def _run_turn(
        self,
        agent: Agent,
        messages: list,
        tools: dict[str, Tool],
        provider,
    ):
        tool_results: list = []
        completion = await provider.create(
            messages=[m.model_dump() for m in messages],  # type: ignore
            tools=[t.formatted_schema() for t in tools.values()],  # type: ignore
            **self.model_params(agent),
        )

        message = completion.choices[0].message
        if message.tool_calls:
            tool_results = [
                r
                async for r in self._executor.execute_calls(
                    message.tool_calls, tools
                )
            ]
        return completion, message, tool_results

    def run_sync(
        self,
        agent: Agent,
        input: str,
        env: SessionContext,
        options: RunOptions | None = None,
    ) -> RunResult:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            if loop.is_running():
                raise RuntimeError(
                    "Cannot call run_sync from within an async context. Use run instead."
                )

        return asyncio.run(self.run(agent, input, env, options))

    def model_params(self, agent: Agent):
        params: dict[str, Any] = {}
        if agent.temperature is not None:
            params["temperature"] = agent.temperature
        if agent.output_type is not None:
            schema = agent.output_type.model_json_schema()
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "final_output",
                    "strict": True,
                    "schema": schema,
                },
            }
        return params

    def _format_output(
        self, agent: Agent, content: str | None
    ) -> str | BaseModel | None:
        if content is None:
            return None
        if agent.output_type is not None:
            return agent.output_type.model_validate_json(content)
        return content

    async def _build_messages(
        self, agent: Agent, history: Messages, ctx: SessionContext
    ) -> list:
        messages = await history.get()
        if agent.instruction:
            messages += [SystemMessage(content=agent.instruction)]
        if ctx.ext_runner is not None:
            skill_prompt = agent.skill_prompt(ctx.ext_runner.skills)
            if skill_prompt:
                messages += [SystemMessage(content=skill_prompt)]
        return messages

    def _build_tools(self, agent: Agent, source: list[Tool]) -> dict[str, Tool]:
        # 按 agent 的可用工具（源经 tool_filter 过滤）构造工具表；工具不经
        # agent 配置——源来自会话扩展 runner，每轮实时取。
        tools: dict[str, Tool] = {}
        for t in agent.available_tools(source):
            tools[t.name] = t
        for h in agent.handoffs:
            t = h.as_tool()
            tools[t.name] = t
        return tools
