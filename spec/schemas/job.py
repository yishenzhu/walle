"""后台作业模型：跨轮存活的异步任务的数据视图与工具返回。

- `Job` / `JobStatus`：作业状态机，由 background 元工具登记（pending），
  executor 拉起（running），job_result 读取（done / error）。
- `JobDispatch` / `JobResult`：background / job_result 两个内置工具的返回。

作为值类型跨层流动，故置于 schemas。
"""

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


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


class JobDispatch(BaseModel):
    """background 工具返回：作业派发结果，成功即拿到 job_id。"""

    job_id: str
    status: str = "running"  # 派发成功即 running（待 executor 拉起）
    error: str | None = None  # 非空表示派发失败


class JobResult(BaseModel):
    """job_result 工具返回：作业状态与结果。"""

    job_id: str
    status: str  # running | done | error
    result: Any = None  # done：执行结果
    error: str | None = None  # error：错误信息
