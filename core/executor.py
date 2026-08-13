import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from ..schemas import ToolResult, ToolStart
from ..channel import Channel
from .approval import ApprovalPolicy, Approver, ChannelApprover
from ..conf import ApprovalDecision, ToolConfig
from ..infra import TOOL_CALLS, TOOL_ERRORS, TOOL_DURATION, tracer
from ..tools import Tool, ToolContext, tool_context, Job, JobStatus

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

    async def execute_call(
        self,
        tc,
        tools: dict[str, Tool],
        ctx: ToolContext,
    ) -> tuple[str, Any]:
        """入口·回调对象：执行一个模型工具调用（tc 为 LLM 回调对象，含 id/function）。"""
        name = tc.function.name
        args = json.loads(tc.function.arguments)
        return await self.execute_tool(name, args, tc.id, tools, ctx)

    async def execute_named(
        self,
        name: str,
        args: dict[str, Any],
        tools: dict[str, Tool],
        ctx: ToolContext,
    ) -> tuple[str, Any]:
        """入口·名字+参数：按工具名执行（复用 execute_tool 完整链：审批/超时/通知）。

        background 元工具用它把任意工具丢到后台；tc_id 为生成的伪调用 id。
        """
        tc_id = f"bg-{uuid.uuid4().hex[:8]}"
        return await self.execute_tool(name, args, tc_id, tools, ctx)

    async def execute_tool(
        self,
        name: str,
        args: dict[str, Any],
        tc_id: str,
        tools: dict[str, Tool],
        ctx: ToolContext,
    ) -> tuple[str, Any]:
        """核心链：查找 → 通知 → 审批 → 执行（超时）→ 结果/错误。

        execute_call / execute_named 两个入口共用；参数已解包
        （name/args/tc_id），不依赖模型回调对象结构。
        """
        ft = tools.get(name)
        if ft is None:
            reason = f"Error: Unknown tool {name}"
            logger.warning(reason)
            return tc_id, reason

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
            *[self.execute_call(tc, tools, ctx) for tc in tool_calls]
        )

    async def execute_iter(
        self,
        tool_calls: list,
        tools: dict[str, Tool],
        ctx: ToolContext,
    ) -> AsyncIterator[tuple[str, Any]]:
        for task in asyncio.as_completed(
            [self.execute_call(tc, tools, ctx) for tc in tool_calls]
        ):
            tc_id, result = await task
            yield tc_id, result

    async def launch_pending(self, ctx: ToolContext, tools: dict[str, Tool]) -> None:
        """本轮工具执行完后：把 background 写下的 pending 作业拉起（create_task）。

        job 状态 pending → running；run_job 跑完后写回 result/error
        （供 job_result 读取）。
        """
        for job_id, job in ctx.jobs.items():
            if job.status != JobStatus.PENDING:
                continue
            job.status = JobStatus.RUNNING
            job.task = asyncio.create_task(
                self.run_job(job_id, job, tools, ctx)
            )

    async def run_job(
        self,
        job_id: str,
        job: Job,
        tools: dict[str, Tool],
        ctx: ToolContext,
    ) -> None:
        """后台作业执行体：执行工具并写回结果（done）或错误（error）。

        launch_pending 拉起（create_task）后由本方法跑完。
        """
        try:
            _, result = await self.execute_named(
                job.tool_name, job.args, tools, ctx
            )
            job.result = result
            job.status = JobStatus.DONE
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            job.error = str(exc)
            job.status = JobStatus.ERROR
