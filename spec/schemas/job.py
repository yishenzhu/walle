"""后台作业模型：跨轮存活的异步任务的纯数据视图。

由 background 元工具登记（pending），executor 拉起（running），
job_result 读取（done / error）。作为值类型跨层流动，故置于 schemas。
"""

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


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
