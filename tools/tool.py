"""工具能力面实现：注册 + schema + 执行管线（spec.protocol.ToolService）。"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from fnmatch import fnmatch
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
from ..plugin import Context, plugin
from ..spec import (
    PreExecuteToolEvent,
    ToolRestriction,
    ToolResult,
    ToolResultEvent,
    ToolService,
    ToolStart,
)

logger = logging.getLogger(__name__)


class Tools:
    """ToolService 默认实现：注册表 + 继承/限制 + 执行管线。"""

    def __init__(
        self,
        ctx: Context | None = None,
        tool_config: ToolConfig | None = None,
        *,
        base: Tools | None = None,
        restriction: ToolRestriction | None = None,
    ) -> None:
        self._ctx = ctx
        self._base = base
        self._restrictions: list[ToolRestriction] = (
            [restriction] if restriction is not None else []
        )
        self._tools: list[Tool] = []
        self._timeout_policy = (tool_config or ToolConfig()).timeout

    # ── 注册 ──────────────────────────────────────────
    def add(self, *tools: Tool) -> None:
        """注册工具：同名后到者覆盖先到者。同批重名视为编程错误。"""
        names = [t.name for t in tools]
        if len(set(names)) != len(names):
            raise ValueError(f"同名工具同批注册: {names}")
        fresh = {t.name: t for t in tools}
        self._tools = [t for t in self._tools if t.name not in fresh]
        self._tools.extend(fresh.values())

    def remove(self, name: str) -> None:
        self._tools = [t for t in self._tools if t.name != name]

    def has(self, name: str) -> bool:
        return self._lookup(name) is not None

    def all_tools(self) -> list[Tool]:
        """本层注册 + 继承层（同名本层覆盖）。"""
        return self._all_tools()

    def restrict(self, restriction: ToolRestriction) -> Callable[[], None]:
        """对继承来的工具追加一层限制（多层取交集）；返回撤销函数。"""
        self._restrictions.append(restriction)

        def _off() -> None:
            if restriction in self._restrictions:
                self._restrictions.remove(restriction)

        return _off

    # ── schema ────────────────────────────────────────
    def schemas(
        self,
        *,
        restriction: ToolRestriction | None = None,
        tools: dict[str, Tool] | None = None,
    ) -> list[dict[str, Any]]:
        """工具 schema 列表（喂 LLM），按作用域限制投影。"""
        source = {t.name: t for t in self._all_tools(tools)}
        return [
            tool.to_schema()
            for name, tool in source.items()
            if self._visible(name, restriction)
        ]

    # ── 执行 ──────────────────────────────────────────
    async def execute(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        tool_call_id: str = "",
        *,
        restriction: ToolRestriction | None = None,
        tools: dict[str, Tool] | None = None,
    ) -> Any:
        """执行单个工具调用，返回结果或错误字符串（工具级失败不抛）。"""
        if (tools is None or name not in tools) and not self._visible(name, restriction):
            return f"Error: Unknown tool {name}"
        return await self._run(name, arguments or {}, tool_call_id, notify=True, tools=tools)

    async def launch_pending(self) -> None:
        """本轮工具执行完后：把 background 写下的 pending 作业拉起。"""
        ctx = tool_context.get()
        if ctx is None:
            return
        for job_id, job in ctx.jobs.items():
            if job.status != JobStatus.PENDING:
                continue
            job.status = JobStatus.RUNNING
            job.task = asyncio.create_task(self.run_job(job_id, job, ctx))

    async def run_job(self, job_id: str, job: Job, ctx: SessionView) -> None:
        """后台作业执行体：静默执行工具并写回结果（done）或错误（error）。"""
        tool_context.set(ctx)
        try:
            tc_id = f"bg-{uuid.uuid4().hex[:8]}"
            result = await self._run(
                job.tool_name, job.args, tc_id, notify=False, tools=None
            )
            job.result = result
            job.status = JobStatus.DONE
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 后台作业错误写回 job
            job.error = str(exc)
            job.status = JobStatus.ERROR

    async def _run(
        self,
        name: str,
        args: dict[str, Any],
        tool_call_id: str,
        *,
        notify: bool,
        tools: dict[str, Tool] | None,
    ) -> Any:
        """执行核心：查找 → 通知 → 裁决 → 超时执行 → 结果通知。"""
        tool = self._lookup(name, tools)
        channel = self._channel()
        if tool is not None and notify and channel is not None:
            await channel.notify(
                ToolStart(tool_name=name, arguments=args, tool_call_id=tool_call_id)
            )

        result = error = None
        if tool is None:
            error = f"Error: Unknown tool {name}"
        else:
            blocked = None
            if self._ctx is not None:
                args, blocked = await self._vet(name, args, tool_call_id)
            if blocked is not None:
                error = blocked
            else:
                result, error = await self._execute(tool, name, args)

        if self._ctx is not None:
            await self._ctx.emit(
                ToolResultEvent(
                    name=name,
                    arguments=args,
                    tool_call_id=tool_call_id,
                    result=result,
                    error=error,
                )
            )
        if tool is not None and notify and channel is not None:
            await channel.notify(
                ToolResult(tool_call_id=tool_call_id, result=result, error=error)
            )
        return error if error is not None else result

    async def _execute(
        self, tool: Tool, name: str, args: dict[str, Any]
    ) -> tuple[Any, str | None]:
        """跑工具本体：超时策略 + 遥测 span/指标；返回 (result, error)。"""
        attrs = {"tool.name": name}
        try:
            with tracer.start_as_current_span("tool.execute") as span:
                span.set_attribute("tool.name", name)
                start = time.monotonic()
                result = await asyncio.wait_for(
                    tool.run(args), self._timeout_policy.resolve(name)
                )
                elapsed_ms = (time.monotonic() - start) * 1000
            TOOL_DURATION.record(elapsed_ms, attrs)
            TOOL_CALLS.add(1, attrs)
            logger.debug(f"{name}: {elapsed_ms:.0f}ms")
            return result, None
        except TimeoutError:
            timeout = self._timeout_policy.resolve(name)
            logger.warning(f"{name}: timeout after {timeout}s")
            TOOL_ERRORS.add(1, attrs)
            return None, f"Error: tool '{name}' timed out after {timeout}s"
        except Exception as exc:  # noqa: BLE001 - 工具异常转错误结果
            logger.warning(f"{name}: {exc}")
            TOOL_ERRORS.add(1, attrs)
            return None, f"Error: {exc}"

    def _channel(self):
        ctx = tool_context.get()
        return ctx.channel if ctx is not None else None

    async def _vet(
        self, name: str, args: dict[str, Any], tool_call_id: str
    ) -> tuple[dict[str, Any], str | None]:
        """预执行裁决：bail 取首个裁决；支持 HookVerdict 与 dict 两种表态。"""
        verdict = await self._ctx.bail(
            PreExecuteToolEvent(name=name, arguments=args, tool_call_id=tool_call_id)
        )
        if isinstance(verdict, HookVerdict):
            if verdict.blocks:
                blocked = f"Tool '{name}' blocked by plugin"
                if verdict.block:
                    blocked += f": {verdict.block}"
                logger.info(blocked)
                return args, blocked
            if verdict.arguments is not None:
                return verdict.arguments, None
            return args, None
        if isinstance(verdict, dict) and "block" in verdict:
            blocked = f"Tool '{name}' blocked by plugin"
            if verdict["block"]:
                blocked += f": {verdict['block']}"
            logger.info(blocked)
            return args, blocked
        if isinstance(verdict, dict) and isinstance(verdict.get("arguments"), dict):
            return verdict["arguments"], None
        return args, None

    # ── 可见性 ────────────────────────────────────────
    def _all_tools(self, extra: dict[str, Tool] | None = None) -> list[Tool]:
        merged: dict[str, Tool] = {}
        if self._base is not None:
            merged.update({t.name: t for t in self._base.all_tools()})
        merged.update({t.name: t for t in self._tools})
        if extra:
            merged.update(extra)
        return list(merged.values())

    def _lookup(
        self, name: str, extra: dict[str, Tool] | None = None
    ) -> Tool | None:
        if extra and name in extra:
            return extra[name]
        return next((t for t in self._all_tools() if t.name == name), None)

    def _own_names(self) -> set[str]:
        return {t.name for t in self._tools}

    def _visible(self, name: str, restriction: ToolRestriction | None) -> bool:
        """可见性：deny 优先；allow 只约束继承来的工具。"""
        restrictions = [*self._restrictions]
        if restriction is not None:
            restrictions.append(restriction)
        if any(_denies(name, r) for r in restrictions):
            return False
        if name in self._own_names():
            return True
        if not all(_admits(name, r) for r in restrictions):
            return False
        return self._base._visible(name, None) if self._base is not None else True


def _denies(name: str, restriction: ToolRestriction | None) -> bool:
    if restriction is None:
        return False
    return any(fnmatch(name, pat) for pat in restriction.deny)


def _admits(name: str, restriction: ToolRestriction | None) -> bool:
    if restriction is None or restriction.allow is None:
        return True
    return any(fnmatch(name, pat) for pat in restriction.allow)


@plugin(name="tools", provides=(ToolService,))
async def native_tool_runtime(ctx, config) -> None:
    """工具运行时默认实现：发布 ToolService 能力。"""
    await ctx.register(ToolService, Tools(ctx, config.get("tool_config")))


@plugin(name="tool")
async def native_tool_scope(ctx, config) -> None:
    """工具筛选插件（agent 级）：对所在作用域继承来的工具施加限制。"""
    restriction = _restriction(config)
    if restriction is None:
        return
    ctx.get(ToolService).restrict(restriction)


def _restriction(config) -> ToolRestriction | None:
    if config is None or config == {}:
        return None
    if isinstance(config, dict):
        if "allow" not in config and not config.get("deny"):
            return None
        return ToolRestriction(
            allow=config.get("allow"),
            deny=list(config.get("deny") or []),
        )
    return ToolRestriction(allow=list(config))
