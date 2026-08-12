import asyncio
import logging
from dataclasses import dataclass
from pydantic import BaseModel
from typing import Any

from .agent import Agent, TContext, Handoff
from .executor import ToolExecutor
from ..channel import Channel
from ..messages import Messages, InMemoryMessages
from ..infra import OpenAIProvider, tracer, AGENT_ITERATIONS, HANDOFF
from ..schemas import (
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    Usage,
    UserMessage,
    Delta,
    DeltaEnd,
    ToolResult,
)
from ..tools import ToolContext, Tool
from ..infra import PyKernel


logger = logging.getLogger(__name__)

DEFAULT_MAX_TURNS = 10


@dataclass
class RunOptions:
    """单次 run 的可选行为配置（怎么做），与会话环境（SessionEnv）分离。"""

    max_turns: int = DEFAULT_MAX_TURNS
    streamed: bool = False      # 流式输出（delta 通知）


@dataclass
class SessionEnv:
    """会话级环境与状态：Session 唯一持有，每次 run 原样传入。"""

    kernel: PyKernel                  # 会话级有状态解释器（必填）
    messages: Messages                # 会话历史（必填）
    provider: OpenAIProvider = None         # 模型接入（None 用 Runner 默认）
    channel: Channel = None          # 会话 channel 端点


class RunResult(BaseModel):
    input: str
    last_agent: Agent[Any] | None = None
    max_turns: int
    completed_turns: int = 0
    output: str | BaseModel | None = None

    model_config = {"arbitrary_types_allowed": True}


class Runner:
    """Agent 执行器：持有默认 provider / 工具执行器，env 未提供时复用。"""

    def __init__(
        self,
        provider: OpenAIProvider | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        # 默认实例（复用，避免每次 run 新建）
        self._provider = provider or OpenAIProvider.get_default()
        self._executor = executor or ToolExecutor()

    async def run(
        self,
        agent: Agent[TContext],
        input: str,
        env: SessionEnv,
        options: RunOptions | None = None,
    ) -> RunResult:
        options = options or RunOptions()
        provider = env.provider or self._provider
        if provider is None:
            raise RuntimeError("no invalid provider")
        channel, history, kernel = env.channel, env.messages, env.kernel
        streamed = options.streamed
        await history.add([UserMessage(content=input)])

        with tracer.start_as_current_span("agent.run") as span:
            span.set_attribute("streamed", streamed)
            turn = 0
            while turn < options.max_turns:
                turn += 1
                span.set_attribute("agent.turn", turn)
                model = provider.model
                span.set_attribute("agent.model", model)

                messages = await self._build_messages(agent, history)
                tools = self._build_tools(agent)

                run_turn = self._run_turn_streamed if streamed else self._run_turn
                completion, message, tool_results = await run_turn(
                    agent, model, messages, tools, kernel, channel, provider
                )

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
                    return RunResult(
                        input=input,
                        last_agent=agent,
                        output=output,
                        max_turns=options.max_turns,
                        completed_turns=turn,
                    )

            AGENT_ITERATIONS.record(turn)
            span.set_attribute("agent.iterations", turn)
            logger.warning(f"max turns ({options.max_turns}) reached. Stopping.")
            return RunResult(
                input=input,
                last_agent=agent,
                max_turns=options.max_turns,
                completed_turns=turn,
            )

    async def _run_turn_streamed(
        self,
        agent: Agent[Any],
        model: str,
        messages: list,
        tools: dict[str, Tool],
        kernel,
        channel,
        provider,
    ):
        tool_results: list = []
        async with provider.client.chat.completions.stream(
            model=model,
            messages=[m.model_dump() for m in messages],  # type: ignore
            tools=[t.formatted_schema() for t in tools.values()],  # type: ignore
            **self.model_params(agent),
        ) as stream:
            async for event in stream:
                if event.type == "content.delta" and channel:
                    await channel.notify(Delta(delta=event.delta))

            completion = await stream.get_final_completion()
            message = completion.choices[0].message
            if message.tool_calls:
                ctx = ToolContext(kernel=kernel, channel=channel)
                async for tc_id, r in self._executor.execute_iter(
                    message.tool_calls, tools, ctx
                ):
                    tool_results.append((tc_id, r))
                    if channel:
                        await channel.notify(
                            ToolResult(tool_call_id=tc_id, result=r)
                        )
            elif channel:
                await channel.notify(DeltaEnd())
        return completion, message, tool_results

    async def _run_turn(
        self,
        agent: Agent[Any],
        model: str,
        messages: list,
        tools: dict[str, Tool],
        kernel,
        channel,
        provider,
    ):
        tool_results: list = []
        completion = await provider.client.chat.completions.create(
            model=model,
            messages=[m.model_dump() for m in messages],  # type: ignore
            tools=[t.formatted_schema() for t in tools.values()],  # type: ignore
            **self.model_params(agent),
        )

        message = completion.choices[0].message
        if message.tool_calls:
            ctx = ToolContext(kernel=kernel, channel=channel)
            tool_results = await self._executor.execute_batch(
                message.tool_calls, tools, ctx
            )
        return completion, message, tool_results

    def run_sync(
        self,
        agent: Agent[TContext],
        input: str,
        env: SessionEnv,
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

    def model_params(self, agent: Agent[Any]):
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
        self, agent: Agent[Any], content: str | None
    ) -> str | BaseModel | None:
        if content is None:
            return None
        if agent.output_type is not None:
            return agent.output_type.model_validate_json(content)
        return content

    async def _build_messages(self, agent: Agent[Any], history: Messages) -> list:
        messages = await history.get()
        if agent.instruction:
            messages += [SystemMessage(content=agent.instruction)]
        return messages

    def _build_tools(self, agent: Agent[Any]) -> dict[str, Tool]:
        # 实时取工具（agent.tools 源反映运行时添加的工具）
        tools: dict[str, Tool] = {}
        if agent.tools is not None:
            tools.update({t.name: t for t in agent.tools()})
        for h in agent.handoffs:
            t = h.as_tool()
            tools[t.name] = t
        return tools
