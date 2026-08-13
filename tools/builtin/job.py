"""后台作业工具对：background（派发）+ job_result（查询取回）。

background 只登记 pending 作业到 ctx.jobs（不碰 executor）；本轮工具执行
完后由 executor.launch_pending 统一拉起，结果用 job_result 稍后查询取回。
"""

from .. import tool_context
from ..tool import JobStatus
from ...schemas import JobDispatch, JobResult


async def background(tool_name: str, args: dict | None = None) -> JobDispatch:
    """把任意工具放到后台异步执行：立即返回 job_id（本轮不阻塞）。

    稍后用 job_result(job_id) 查询/取回结果。适合长任务（子代理、编译、搜索）。
    """
    ctx = tool_context.get()
    if ctx is None:
        return JobDispatch(job_id="", status=JobStatus.ERROR, error="background 不可用：无执行上下文")
    job_id = ctx.add_pending(tool_name, args)
    # 仅登记待启动：executor 在本轮工具跑完后才拉起，故报 pending（非 running）
    return JobDispatch(job_id=job_id, status=JobStatus.PENDING)


async def job_result(job_id: str) -> JobResult:
    """查询后台作业（background 启动）的状态与结果。"""
    ctx = tool_context.get()
    if ctx is None:
        return JobResult(job_id=job_id, status=JobStatus.ERROR, error="无执行上下文")
    job = ctx.jobs.get(job_id)
    if job is None:
        return JobResult(job_id=job_id, status=JobStatus.ERROR, error=f"未知作业: {job_id}")
    if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
        return JobResult(job_id=job_id, status=JobStatus.RUNNING)
    if job.status == JobStatus.ERROR:
        return JobResult(job_id=job_id, status=JobStatus.ERROR, error=job.error)
    # done：取走即删（结果一次性消费）
    result = job.result
    del ctx.jobs[job_id]
    return JobResult(job_id=job_id, status=JobStatus.DONE, result=result)
