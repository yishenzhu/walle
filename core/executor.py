import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from ..conf import ToolConfig
from ..infra import (
    TOOL_CALLS,
    TOOL_DURATION,
    TOOL_ERRORS,
    HookVerdict,
    Job,
    JobStatus,
    SessionView,
    Tool,
    tool_context,
    tracer,
)
from ..spec import (
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolResult,
    ToolStart,
)

logger = logging.getLogger(__name__)


class ToolExecutor:
    """工具执行器：无状态，通知渠道来自每次 execute 的会话上下文。"""

    def __init__(self, config: ToolConfig | None = None):
        config = config or ToolConfig()
        self._timeout_policy = config.timeout

    async def execute_tool_call(
        self,
        tool_call,
        tools: dict[str, Tool],
    ) -> tuple[str, Any]:
        """入口：执行一个模型工具调用（tool_call 含 id/function）。"""
        name = tool_call.function.name
        args = json.loads(tool_call.function.arguments)
        return await self.execute_tool(name, args, tool_call.id, tools)

    async def execute_tool(
        self,
        name: str,
        args: dict[str, Any],
        tc_id: str,
        tools: dict[str, Tool],
        *,
        notify: bool = True,
    ) -> tuple[str, Any]:
        """核心链：查找 → 通知（可关）→ preflight 屏障 → 执行（超时）→ 结果/错误。"""
        ft = tools.get(name)
        if ft is None:
            reason = f"Error: Unknown tool {name}"
            logger.warning(reason)
            return tc_id, reason

        # 会话上下文由调用方（runner 每轮 / run_job 后台任务）注入
        # tool_context——审批插件、preflight 钩子与工具执行都原地获取。
        ctx = tool_context.get()
        channel = ctx.channel if ctx is not None else None

        async def report_error(message: str) -> str:
            """（可选）通知客户端一次工具失败，返回给模型的错误文本。"""
            if notify and channel is not None:
                await channel.notify(ToolResult(tool_call_id=tc_id, error=message))
            return message

        if notify and channel is not None:
            await channel.notify(
                ToolStart(tool_name=name, arguments=args, tool_call_id=tc_id)
            )

        # preflight 屏障：监听器返回 HookVerdict，None 放行。
        # block → 阻止执行；arguments → 按注册顺序改写参数（后者覆盖前者）。
        if ctx is not None and ctx.bus is not None:
            for verdict in await ctx.bus.emit(
                ToolExecutionStartEvent(
                    tool_name=name,
                    arguments=args,
                    tool_call_id=tc_id,
                )
            ):
                if not isinstance(verdict, HookVerdict):
                    continue  # None 放行
                if verdict.blocks:
                    blocked = f"Tool '{name}' blocked by plugin"
                    if verdict.block:
                        blocked += f": {verdict.block}"
                    logger.info(blocked)
                    return tc_id, await report_error(blocked)
                if verdict.arguments is not None:
                    args = verdict.arguments  # 改写本次调用参数

        attrs = {"tool.name": name}
        result = error = None
        elapsed_ms = None
        timeout = self._timeout_policy.resolve(name)
        try:
            with tracer.start_as_current_span("tool.execute") as span:
                span.set_attribute("tool.name", name)
                start = time.monotonic()
                result = await asyncio.wait_for(ft.run(args), timeout)
                elapsed_ms = (time.monotonic() - start) * 1000

            TOOL_DURATION.record(elapsed_ms, attrs)
            TOOL_CALLS.add(1, attrs)
            logger.debug(f"{name}: {elapsed_ms:.0f}ms")
            if notify and channel is not None:
                await channel.notify(ToolResult(tool_call_id=tc_id, result=result))
        except TimeoutError:
            logger.warning(f"{name}: timeout after {timeout}s")
            TOOL_ERRORS.add(1, attrs)
            elapsed_ms = timeout * 1000
            error = await report_error(f"Error: tool '{name}' timed out after {timeout}s")
        except Exception as e:
            logger.warning(f"{name}: {e}")
            TOOL_ERRORS.add(1, attrs)
            error = await report_error(f"Error: {e}")
        finally:
            # after_tool_call 通知（非阻断）：携带结果 / 错误 / 耗时供观测型插件。
            # 工具未找到 / 审批拒绝 / 被插件阻止的路径不经过 try，不发 after。
            if ctx is not None and ctx.bus is not None:
                await ctx.bus.emit(
                    ToolExecutionEndEvent(
                        tool_name=name,
                        tool_call_id=tc_id,
                        result=result,
                        error=error,
                        elapsed_ms=elapsed_ms,
                    )
                )
        return tc_id, result if error is None else error

    async def execute_tool_calls(
        self,
        tool_calls: list,
        tools: dict[str, Tool],
    ) -> AsyncIterator[tuple[str, Any]]:
        """并发执行一批工具调用，按完成序逐个产出（runner 流式/非流式共用）。"""
        tasks = [
            asyncio.create_task(self.execute_tool_call(tool_call, tools))
            for tool_call in tool_calls
        ]
        try:
            for fut in asyncio.as_completed(tasks):
                tc_id, result = await fut
                yield tc_id, result
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def launch_pending_jobs(self, tools: dict[str, Tool]) -> None:
        """本轮工具执行完后：把 background 写下的 pending 作业拉起（create_task）。"""
        ctx = tool_context.get()
        if ctx is None:
            return
        for job_id, job in ctx.jobs.items():
            if job.status != JobStatus.PENDING:
                continue
            job.status = JobStatus.RUNNING
            job.task = asyncio.create_task(self.run_job(job_id, job, tools, ctx))

    async def run_job(
        self,
        job_id: str,
        job: Job,
        tools: dict[str, Tool],
        ctx: SessionView,
    ) -> None:
        """后台作业执行体：执行工具并写回结果（done）或错误（error）。"""
        tool_context.set(ctx)
        try:
            tc_id = f"bg-{uuid.uuid4().hex[:8]}"
            _, result = await self.execute_tool(
                job.tool_name, job.args, tc_id, tools, notify=False
            )
            job.result = result
            job.status = JobStatus.DONE
        except Exception as exc:
            job.error = str(exc)
            job.status = JobStatus.ERROR
