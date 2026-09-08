"""核心数据类型：工具（Tool）与工具执行上下文（ToolContext）。"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from mcp.server.fastmcp.tools import Tool as MCPTool

from ..channel import Channel
from ..schemas import Messages, ExtRunner
from .event_bus import EventBus


class JobStatus(StrEnum):
    """后台作业状态。"""

    PENDING = "pending"  # 待启动（background 已登记，executor 未拉起）
    RUNNING = "running"  # 运行中（executor 已 create_task）
    DONE = "done"  # 完成（result 可读）
    ERROR = "error"  # 失败（error 可读）


@dataclass
class Job:
    """一个后台作业：pending（待启动）/ running / done / error。

    pending 由 background 元工具写入（记录工具名+参数，待 executor 拉起）；
    running 起 task；done 存 result；error 存错误信息。
    """

    status: JobStatus = JobStatus.PENDING  # 见 JobStatus
    tool_name: str | None = None  # pending 时：要执行的工具名
    args: dict[str, Any] | None = None  # pending 时：工具参数
    task: asyncio.Task | None = None  # running 后：后台任务
    result: Any = None  # done：执行结果
    error: str | None = None  # error：错误信息


@dataclass
class ToolContext:
    # 会话 channel：工具按需发起 notify / call（如 ask_user 提问）
    channel: Channel | None = None
    # 后台作业表：跨轮存活（Session 持有并传入），job_id → Job
    jobs: dict[str, Job] = field(default_factory=dict)
    # 进程级事件总线：工具执行钩子（before/after）屏障来源
    bus: EventBus | None = None
    # 会话扩展激活层（ExtRunner 能力面）：动态工具（define_tool）经此注册
    ext: ExtRunner | None = None
    # 会话工作目录（bash 执行位置 / 沙箱可写区；无则不设）
    cwd: str | None = None
    # 会话历史存储（history 回源工具读底层原文；只读查询）
    history: Messages | None = None

    def add_pending(self, tool_name: str, args: dict[str, Any] | None = None) -> str:
        """登记一个待启动的后台作业（executor 在本轮工具跑完后拉起）。"""
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        self.jobs[job_id] = Job(
            status=JobStatus.PENDING, tool_name=tool_name, args=args or {}
        )
        return job_id


tool_context: ContextVar[ToolContext | None] = ContextVar("tool_context", default=None)


@dataclass
class Tool:
    """一个可被模型调用的工具：名称/描述/参数 schema/执行函数。

    纯数据 + 行为，不需要 pydantic 序列化（schema 由 formatted_schema 生成）。
    """

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[[dict[str, Any]], Awaitable[Any]]

    async def run(self, args: dict[str, Any]) -> Any:
        return await self.fn(args)

    def formatted_schema(self, strict: bool = True) -> dict[str, Any]:
        args = self.parameters.copy()
        if strict:
            args["additionalProperties"] = False
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "strict": strict,
                "parameters": args,
            },
        }

    @classmethod
    def from_function(
        cls,
        fn: Callable,
        name: str | None = None,
        description: str | None = None,
    ):
        tool = MCPTool.from_function(fn, name=name, description=description)
        return cls(
            name=name or tool.name,
            description=description or tool.description,
            parameters=tool.parameters,
            fn=tool.run,
        )
