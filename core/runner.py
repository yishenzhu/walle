import asyncio
import logging
from pydantic import BaseModel
from typing import Any

from ..schemas.channel import TextDeltaEnd

from .agent import Agent, TContext, Handoff
from .executor import ToolExecutor
from ..session import Session, InMemorySession
from ..infra import OpenAIProvider, tracer, AGENT_ITERATIONS, HANDOFF
from ..schemas import (
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    Usage,
    UserMessage,
    TextDelta,
)
from ..channel import Channel
from ..tools import ToolContext, Tool


logger = logging.getLogger(__name__)

DEFAULT_MAX_TURNS = 10


class RunConfig(BaseModel):
    model: str | None = None
    max_turns: int = DEFAULT_MAX_TURNS


class RunResult(BaseModel):
    input: str
    last_agent: Agent[Any] | None = None
    max_turns: int
    completed_turns: int = 0
    output: str | BaseModel | None = None

    model_config = {"arbitrary_types_allowed": True}


class Runner:
    def __init__(
        self,
        channel: Channel | None = None,
        config: RunConfig | None = None,
        provider: OpenAIProvider | None = None,
        session: Session | None = None,
        tool_executor: ToolExecutor | None = None,
    ):
        self._channel = channel
        self._config = config or RunConfig()
        provider = provider or OpenAIProvider.get_default()
        if provider is None:
            raise RuntimeError("no invalid provider")
        self._provider = provider
        self._session = session or InMemorySession()
        self._executor = tool_executor or ToolExecutor()

    def _tool_context(self):
        return ToolContext(
            channel=self._channel,
            session=self._session,
            provider=self._provider,
        )

    async def run(
        self,
        agent: Agent[TContext],
        input: str,
        streamed: bool = False,
    ) -> RunResult:
        await self._session.add([UserMessage(content=input)])

        with tracer.start_as_current_span("agent.run") as span:
            span.set_attribute("streamed", streamed)
            turn = 0
            while turn < self._config.max_turns:
                turn += 1
                span.set_attribute("agent.turn", turn)
                model = self._config.model or agent.model or self._provider.model
                span.set_attribute("agent.model", model)

                await self._apply_injections()
                messages = await self._build_messages(agent)
                tools = self._build_tools(agent)

                run_turn = self._run_turn_streamed if streamed else self._run_turn
                completion, message, tool_results = await run_turn(
                    agent, model, messages, tools
                )

                usage = Usage.model_validate(completion.usage)
                message = AssistantMessage.from_response(message)
                await self._session.add([message], usage=usage)
                await self._session.add(
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
                        max_turns=self._config.max_turns,
                        completed_turns=turn,
                    )

            AGENT_ITERATIONS.record(turn)
            span.set_attribute("agent.iterations", turn)
            logger.warning(f"max turns ({self._config.max_turns}) reached. Stopping.")
            return RunResult(
                input=input,
                last_agent=agent,
                max_turns=self._config.max_turns,
                completed_turns=turn,
            )

    async def _run_turn_streamed(
        self, agent: Agent[Any], model: str, messages: list, tools: dict[str, Tool]
    ):
        tool_results: list = []
        async with self._provider.client.chat.completions.stream(
            model=model,
            messages=[m.model_dump() for m in messages],  # type: ignore
            tools=[t.formatted_schema() for t in tools.values()],  # type: ignore
            **self.model_params(agent),
        ) as stream:
            async for event in stream:
                if event.type == "content.delta" and self._channel:
                    await self._channel.send(TextDelta(delta=event.delta))

            completion = await stream.get_final_completion()
            message = completion.choices[0].message
            if message.tool_calls:
                async for tc_id, r in self._executor.execute_iter(
                    message.tool_calls, tools, self._tool_context()
                ):
                    tool_results.append((tc_id, r))
            elif self._channel:
                await self._channel.send(TextDeltaEnd())
        return completion, message, tool_results

    async def _run_turn(
        self, agent: Agent[Any], model: str, messages: list, tools: dict[str, Tool]
    ):
        tool_results: list = []
        completion = await self._provider.client.chat.completions.create(
            model=model,
            messages=[m.model_dump() for m in messages],  # type: ignore
            tools=[t.formatted_schema() for t in tools.values()],  # type: ignore
            **self.model_params(agent),
        )

        message = completion.choices[0].message
        if message.tool_calls:
            tool_results = await self._executor.execute_batch(
                message.tool_calls, tools, self._tool_context()
            )
        return completion, message, tool_results

    async def _apply_injections(self) -> None:
        if self._channel:
            injections = self._channel.injections()
            if injections:
                await self._session.add(
                    [UserMessage(content=inj.content) for inj in injections]
                )

    def run_sync(
        self,
        agent: Agent[TContext],
        input: str,
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

        return asyncio.run(self.run(agent, input))

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

    async def _build_messages(self, agent: Agent[Any]) -> list:
        messages = await self._session.get()
        if agent.instruction:
            messages += [SystemMessage(content=agent.instruction)]
        return messages

    def _build_tools(self, agent: Agent[Any]) -> dict[str, Tool]:
        tools: dict[str, Tool] = {t.name: t for t in agent.tools}
        for h in agent.handoffs:
            t = h.as_tool()
            tools[t.name] = t
        return tools
