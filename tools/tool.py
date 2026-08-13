import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from mcp.server.fastmcp.tools import Tool as MCPTool
from pydantic import BaseModel

from ..channel import Channel
from ..infra import PyKernel


class JobStatus(StrEnum):
    """后台作业状态。"""

    PENDING = "pending"   # 待启动（background 已登记，executor 未拉起）
    RUNNING = "running"   # 运行中（executor 已 create_task）
    DONE = "done"         # 完成（result 可读）
    ERROR = "error"       # 失败（error 可读）


@dataclass
class Job:
    """一个后台作业：pending（待启动）/ running / done / error。

    pending 由 background 元工具写入（记录工具名+参数，待 executor 拉起）；
    running 起 task；done 存 result；error 存错误信息。
    """

    status: JobStatus = JobStatus.PENDING   # 见 JobStatus
    tool_name: str | None = None            # pending 时：要执行的工具名
    args: dict[str, Any] | None = None      # pending 时：工具参数
    task: asyncio.Task | None = None        # running 后：后台任务
    result: Any = None                      # done：执行结果
    error: str | None = None                # error：错误信息


@dataclass
class ToolContext:
    # 会话 channel：工具按需发起 notify / call（如 ask_user 提问）
    channel: Channel | None = None
    # 会话级计算资源：python 工具的持久解释器（按会话隔离，由 Session 管理）
    kernel: PyKernel | None = None
    # 后台作业表：跨轮存活（Session 持有并传入），job_id → Job
    jobs: dict[str, Job] = field(default_factory=dict)

    def add_pending(self, tool_name: str, args: dict[str, Any] | None = None) -> str:
        """登记一个待启动的后台作业（executor 在本轮工具跑完后拉起）。"""
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        self.jobs[job_id] = Job(
            status=JobStatus.PENDING, tool_name=tool_name, args=args or {}
        )
        return job_id


tool_context: ContextVar[ToolContext | None] = ContextVar("tool_context", default=None)


class Tool(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[[dict[str, Any]], Awaitable[Any]]

    model_config = {"arbitrary_types_allowed": True}

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
