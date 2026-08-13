import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from ..schemas import ToolResult, ToolStart
from ..channel import Channel
from .approval import ApprovalPolicy, Approver, ChannelApprover
from ..conf import ApprovalDecision, ToolConfig
from ..infra import TOOL_CALLS, TOOL_ERRORS, TOOL_DURATION, tracer
from ..tools import Tool, ToolContext, tool_context

logger = logging.getLogger(__name__)


class ToolExecutor:
    """工具执行器：无状态，审批/通知渠道均来自每次 execute 的 ToolContext。"""

    def __init__(self, config: ToolConfig | None = None):
        cfg = config or ToolConfig()   # ToolConfig 自带默认构造（approval + timeout）
        self._approval_policy = ApprovalPolicy(cfg.approval)
        self._timeout_policy = cfg.timeout

    async def _check_approval(
        self,
        name: str,
        args: dict[str, Any],
        tc_id: str,
        approver: Approver | None,
    ) -> str | None:
        decision = self._approval_policy.evaluate(name, args)
        if decision == ApprovalDecision.DENY:
            return f"Tool '{name}' denied by policy"
        if decision == ApprovalDecision.ALLOW:
            return None
        # ASK：审批渠道由调用方实例化 ChannelApprover(channel) 后传入
        #（executor 无状态，每次 execute 现建）
        if approver is None:
            return f"Tool '{name}' denied: no approval channel"
        response = await approver.ask(
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
        channel = ctx.channel

        if channel is not None:
            await channel.notify(
                ToolStart(tool_name=name, arguments=args, tool_call_id=tc_id)
            )

        denied = await self._check_approval(
            name,
            args,
            tc_id,
            ChannelApprover(channel) if channel is not None else None,
        )
        if denied:
            logger.info(denied)
            if channel is not None:
                await channel.notify(
                    ToolResult(tool_call_id=tc_id, error=denied)
                )
            return tc_id, denied

        attrs = {"tool.name": name}
        try:
            tool_context.set(ctx)
            with tracer.start_as_current_span("tool.execute") as span:
                span.set_attribute("tool.name", name)
                start = time.monotonic()
                result = await asyncio.wait_for(
                    ft.run(args), self._timeout_policy.resolve(name)
                )
                elapsed_ms = (time.monotonic() - start) * 1000

            TOOL_DURATION.record(elapsed_ms, attrs)
            TOOL_CALLS.add(1, attrs)
            logger.debug(f"{name}: {elapsed_ms:.0f}ms")
            if channel is not None:
                await channel.notify(ToolResult(tool_call_id=tc_id, result=result))
            return tc_id, result
        except asyncio.TimeoutError:
            timeout = self._timeout_policy.resolve(name)
            logger.warning(f"{name}: timeout after {timeout}s")
            TOOL_ERRORS.add(1, attrs)
            error = f"Error: tool '{name}' timed out after {timeout}s"
            if channel is not None:
                await channel.notify(ToolResult(tool_call_id=tc_id, error=error))
            return tc_id, error
        except Exception as e:
            logger.warning(f"{name}: {e}")
            TOOL_ERRORS.add(1, attrs)
            error = f"Error: {e}"
            if channel is not None:
                await channel.notify(ToolResult(tool_call_id=tc_id, error=error))
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
