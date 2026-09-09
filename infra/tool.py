"""核心数据类型：工具（Tool）与工具执行上下文协议（SessionView）。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

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


class SessionView(Protocol):
    """工具执行期可见的会话状态（SessionContext 即实现）。

    经 tool_context ContextVar 注入；只声明工具需要的属性（能力收窄：
    不含 provider/agents/depth/session_id）。仅用于类型标注，故不加
    runtime_checkable。
    """

    channel: Channel | None
    jobs: dict[str, Job]
    cwd: str | None
    history: Messages
    ext_runner: ExtRunner | None
    bus: EventBus | None


tool_context: ContextVar[SessionView | None] = ContextVar("tool_context", default=None)


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
