import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from ..schemas import ToolResult, ToolStart
from .approval import ApprovalPolicy, Approver
from ..conf import ApprovalDecision, ToolConfig
from ..infra import TOOL_CALLS, TOOL_ERRORS, TOOL_DURATION, tracer
from ..tools import Tool, ToolContext, tool_context

logger = logging.getLogger(__name__)


class ToolExecutor:
    def __init__(
        self,
        config: ToolConfig | None = None,
        channel=None,
        approver: Approver | None = None,
    ):
        self._policy = ApprovalPolicy(config.approval if config else None)
        self._timeout = config.timeout if config else None
        self._channel = channel  # 通知输出（ToolStart / ToolResult），可为 None
        self._approver = approver  # 审批策略，None 时无审批渠道 → 拒绝

    async def _check_approval(
        self, name: str, args: dict[str, Any], tc_id: str
    ) -> str | None:
        decision = self._policy.evaluate(name, args)
        if decision == ApprovalDecision.DENY:
            return f"Tool '{name}' denied by policy"
        if decision == ApprovalDecision.ALLOW:
            return None
        if self._approver is None:
            return f"Tool '{name}' denied: no approval channel"
        response = await self._approver.ask(
            tool_name=name, arguments=args, tool_call_id=tc_id
        )
        if response.approved:
            return None
        reason = f"Tool '{name}' denied by user"
        return f"{reason}: {response.reason}" if response.reason else reason

    async def execute(
        self,
        tc,
        tools: dict[str, Tool],
        ctx: ToolContext,
    ) -> tuple[str, Any]:
        func = tc.function
        name = func.name
        tc_id = tc.id
        ft = tools.get(name)
        if ft is None:
            reason = f"Error: Unknown tool {name}"
            logger.warning(reason)
            return tc_id, reason

        args = json.loads(func.arguments)

        if self._channel is not None:
            await self._channel.notify(
                ToolStart(tool_name=name, arguments=args, tool_call_id=tc_id)
            )

        denied = await self._check_approval(name, args, tc_id)
        if denied:
            logger.info(denied)
            if self._channel is not None:
                await self._channel.notify(
                    ToolResult(tool_call_id=tc_id, error=denied)
                )
            return tc_id, denied

        attrs = {"tool.name": name}
        try:
            tool_context.set(ctx)
            with tracer.start_as_current_span("tool.execute") as span:
                span.set_attribute("tool.name", name)
                start = time.monotonic()
                result = await asyncio.wait_for(ft.run(args), self._timeout)
                elapsed_ms = (time.monotonic() - start) * 1000

            TOOL_DURATION.record(elapsed_ms, attrs)
            TOOL_CALLS.add(1, attrs)
            logger.debug(f"{name}: {elapsed_ms:.0f}ms")
            return tc_id, result
        except asyncio.TimeoutError:
            logger.warning(f"{name}: timeout after {self._timeout}s")
            TOOL_ERRORS.add(1, attrs)
            error = f"Error: tool '{name}' timed out after {self._timeout}s"
            if self._channel is not None:
                await self._channel.notify(ToolResult(tool_call_id=tc_id, error=error))
            return tc_id, error
        except Exception as e:
            logger.warning(f"{name}: {e}")
            TOOL_ERRORS.add(1, attrs)
            error = f"Error: {e}"
            if self._channel is not None:
                await self._channel.notify(ToolResult(tool_call_id=tc_id, error=error))
            return tc_id, error

    async def execute_batch(
        self,
        tool_calls: list,
        tools: dict[str, Tool],
        ctx: ToolContext,
    ) -> list[tuple[str, Any]]:
        return await asyncio.gather(
            *[self.execute(tc, tools, ctx) for tc in tool_calls]
        )

    async def execute_iter(
        self,
        tool_calls: list,
        tools: dict[str, Tool],
        ctx: ToolContext,
    ) -> AsyncIterator[tuple[str, Any]]:
        for task in asyncio.as_completed(
            [self.execute(tc, tools, ctx) for tc in tool_calls]
        ):
            tc_id, result = await task
            yield tc_id, result
