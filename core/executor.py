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
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    tool_context,
    tracer,
)
from ..spec import ToolResult, ToolStart

logger = logging.getLogger(__name__)


class ToolExecutor:
    """工具执行器：无状态，通知渠道来自每次 execute 的会话上下文。

    审批不在此内置——由审批扩展（tools.approval.Approval）订阅
    TOOL_EXECUTION_START 承担（preflight 事件是唯一审批屏障）。
    """

    def __init__(self, config: ToolConfig | None = None):
        cfg = config or ToolConfig()
        self._timeout_policy = cfg.timeout

    async def execute_call(
        self,
        tc,
        tools: dict[str, Tool],
    ) -> tuple[str, Any]:
        """入口·回调对象：执行一个模型工具调用（tc 为 LLM 回调对象，含 id/function）。"""
        name = tc.function.name
        args = json.loads(tc.function.arguments)
        return await self.execute_tool(name, args, tc.id, tools)

    async def execute_tool(
        self,
        name: str,
        args: dict[str, Any],
        tc_id: str,
        tools: dict[str, Tool],
        *,
        notify: bool = True,
    ) -> tuple[str, Any]:
        """核心链：查找 → 通知（可关）→ preflight 屏障 → 执行（超时）→ 结果/错误。

        execute_call（解包模型回调）与 run_job（后台作业）共用；参数已解包
        （name/args/tc_id），不依赖模型回调对象结构。notify=False 时
        静默执行（后台作业用，不推送 ToolStart/ToolResult）。审批由
        TOOL_EXECUTION_START 上的审批扩展（tools.approval.Approval）承担。
        """
        ft = tools.get(name)
        if ft is None:
            reason = f"Error: Unknown tool {name}"
            logger.warning(reason)
            return tc_id, reason

        # 会话上下文由调用方（runner 每轮 / run_job 后台任务）注入
        # tool_context——审批扩展、preflight 钩子与工具执行都原地获取。
        ctx = tool_context.get()
        channel = ctx.channel if ctx is not None else None

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
                    blocked = f"Tool '{name}' blocked by extension"
                    if verdict.block:
                        blocked += f": {verdict.block}"
                    logger.info(blocked)
                    if notify and channel is not None:
                        await channel.notify(
                            ToolResult(tool_call_id=tc_id, error=blocked)
                        )
                    return tc_id, blocked
                if verdict.arguments is not None:
                    args = verdict.arguments  # 改写本次调用参数

        attrs = {"tool.name": name}
        result = error = None
        elapsed_ms = None
        try:
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
            if notify and channel is not None:
                await channel.notify(ToolResult(tool_call_id=tc_id, result=result))
        except TimeoutError:
            timeout = self._timeout_policy.resolve(name)
            logger.warning(f"{name}: timeout after {timeout}s")
            TOOL_ERRORS.add(1, attrs)
            elapsed_ms = timeout * 1000
            error = f"Error: tool '{name}' timed out after {timeout}s"
            if notify and channel is not None:
                await channel.notify(ToolResult(tool_call_id=tc_id, error=error))
        except Exception as e:
            logger.warning(f"{name}: {e}")
            TOOL_ERRORS.add(1, attrs)
            error = f"Error: {e}"
            if notify and channel is not None:
                await channel.notify(ToolResult(tool_call_id=tc_id, error=error))
        finally:
            # after_tool_call 通知（非阻断）：携带结果 / 错误 / 耗时供观测型扩展。
            # 工具未找到 / 审批拒绝 / 被扩展阻止的路径不经过 try，不发 after。
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

    async def execute_calls(
        self,
        tool_calls: list,
        tools: dict[str, Tool],
    ) -> AsyncIterator[tuple[str, Any]]:
        """并发执行一批工具调用，按完成序逐个产出（runner 流式/非流式共用）。"""
        for task in asyncio.as_completed(
            [self.execute_call(tc, tools) for tc in tool_calls]
        ):
            tc_id, result = await task
            yield tc_id, result

    async def launch_pending(self, tools: dict[str, Tool]) -> None:
        """本轮工具执行完后：把 background 写下的 pending 作业拉起（create_task）。

        job 状态 pending → running；run_job 跑完后写回 result/error
        （供 job_result 读取）。ctx 原地取自 tool_context。
        """
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
        """后台作业执行体：执行工具并写回结果（done）或错误（error）。

        launch_pending 拉起（create_task）后由本方法跑完。后台任务独立
        context——开头注入 tool_context，工具/审批扩展经它拿会话上下文。
        """
        tool_context.set(ctx)
        try:
            tc_id = f"bg-{uuid.uuid4().hex[:8]}"
            _, result = await self.execute_tool(
                job.tool_name, job.args, tc_id, tools, notify=False
            )
            job.result = result
            job.status = JobStatus.DONE
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            job.error = str(exc)
            job.status = JobStatus.ERROR
