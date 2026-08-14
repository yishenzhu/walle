"""后台作业全链路测试：background 派发 → launch_pending 拉起 → job_result 取回。

覆盖：
- background 登记 pending、job_result 查询 running/done/error、取走即删
- executor.launch_pending 拉起 + run_job 静默执行（后台不推送通知）
- 集成：Runner 两轮模型驱动（第一轮派发 background、第二轮 job_result 取回）
- Session close 取消未完成作业
"""
import asyncio
import json

import pytest

from ..conf import ApprovalConfig, ApprovalDecision, ToolConfig
from ..core import Agent, Runner, Session, SessionEnv, ToolExecutor
from ..infra import OpenAIProvider, PyKernel
from ..messages import InMemoryMessages
from ..schemas import ToolStart, ToolResult
from ..tools import JobStatus, Tool, ToolContext, tool_context
from ..tools.builtin import background, job_result

from .conftest import (
    FakeChannel,
    FakeCompletion,
    FakeMessage,
    FakeProvider,
    FakeToolCall,
)


def allow_executor() -> ToolExecutor:
    return ToolExecutor(ToolConfig(
        approval=ApprovalConfig(default=ApprovalDecision.ALLOW),
    ))


def make_tool(name: str, result: str = "ok", delay: float = 0.0) -> Tool:
    async def fn(args):
        if delay:
            await asyncio.sleep(delay)
        return result

    return Tool(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}},
        fn=fn,
    )


def make_ctx(jobs: dict | None = None, channel=None) -> ToolContext:
    return ToolContext(channel=channel, jobs=jobs or {})


def tool_call(name: str, arguments: str, id: str = "tc-1") -> FakeToolCall:
    return FakeToolCall(id, name, arguments)


async def wait_job(jobs: dict, job_id: str, target: JobStatus, timeout: float = 5.0):
    """轮询等待作业到达目标状态。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if job_id not in jobs:
            return
        if jobs[job_id].status == target:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"job {job_id} 未在 {timeout}s 内到达 {target}")


# ── 单元级：background / job_result ─────────────────────────────────────────

class TestBackgroundDispatch:
    async def test_background_registers_pending(self):
        ctx = make_ctx()
        token = tool_context.set(ctx)
        try:
            rsp = await background(tool_name="bash", args={"cmd": "ls"})
        finally:
            tool_context.reset(token)
        assert rsp.status == JobStatus.PENDING
        assert rsp.job_id in ctx.jobs
        job = ctx.jobs[rsp.job_id]
        assert job.status == JobStatus.PENDING
        assert job.tool_name == "bash"
        assert job.args == {"cmd": "ls"}

    async def test_background_no_context_errors(self):
        rsp = await background(tool_name="bash")
        assert rsp.status == JobStatus.ERROR
        assert rsp.job_id == ""

    async def test_job_result_unknown_job(self):
        ctx = make_ctx()
        token = tool_context.set(ctx)
        try:
            rsp = await job_result("job_nope")
        finally:
            tool_context.reset(token)
        assert rsp.status == JobStatus.ERROR

    async def test_job_result_running_then_done_consume_once(self):
        executor = allow_executor()
        ctx = make_ctx()
        token = tool_context.set(ctx)
        try:
            rsp = await background(tool_name="echo", args={})
            # 尚未拉起 → running
            q = await job_result(rsp.job_id)
            assert q.status == JobStatus.RUNNING

            await executor.launch_pending(ctx, {"echo": make_tool("echo", "hello")})
            await wait_job(ctx.jobs, rsp.job_id, JobStatus.DONE)

            q = await job_result(rsp.job_id)
            assert q.status == JobStatus.DONE
            assert q.result == "hello"
            # 取走即删：结果一次性消费
            assert rsp.job_id not in ctx.jobs
        finally:
            tool_context.reset(token)


# ── executor：launch_pending / run_job ──────────────────────────────────────

class TestLaunchPending:
    async def test_launch_pending_sets_running_and_task(self):
        executor = allow_executor()
        ctx = make_ctx()
        token = tool_context.set(ctx)
        try:
            rsp = await background(tool_name="slow", args={})
            await executor.launch_pending(ctx, {"slow": make_tool("slow", "s-done", delay=0.05)})
            job = ctx.jobs[rsp.job_id]
            assert job.status == JobStatus.RUNNING
            assert job.task is not None
            await wait_job(ctx.jobs, rsp.job_id, JobStatus.DONE)
            assert ctx.jobs[rsp.job_id].result == "s-done"
        finally:
            tool_context.reset(token)

    async def test_background_runs_silently_no_notify(self):
        """后台作业静默执行：不推送 ToolStart/ToolResult（结果由 job_result 取回）。"""
        executor = allow_executor()
        channel = FakeChannel()
        ctx = make_ctx(channel=channel)
        token = tool_context.set(ctx)
        try:
            rsp = await background(tool_name="echo", args={})
            await executor.launch_pending(ctx, {"echo": make_tool("echo", "hi")})
            await wait_job(ctx.jobs, rsp.job_id, JobStatus.DONE)
            assert not any(isinstance(e, (ToolStart, ToolResult)) for e in channel.events)
        finally:
            tool_context.reset(token)

    async def test_job_error_returned_as_result(self):
        """工具失败不抛异常：execute_tool 转成 "Error: ..." 字符串结果，job 为 DONE。"""
        async def boom(args):
            raise RuntimeError("kaboom")

        executor = allow_executor()
        ctx = make_ctx()
        token = tool_context.set(ctx)
        try:
            rsp = await background(tool_name="boom", args={})
            await executor.launch_pending(ctx, {
                "boom": Tool(name="boom", description="boom",
                             parameters={"type": "object", "properties": {}},
                             fn=boom),
            })
            await wait_job(ctx.jobs, rsp.job_id, JobStatus.DONE)
            assert ctx.jobs[rsp.job_id].result == "Error: kaboom"
            q = await job_result(rsp.job_id)
            assert q.status == JobStatus.DONE
            assert "kaboom" in q.result
        finally:
            tool_context.reset(token)

    async def test_pending_not_launched_skipped(self):
        """已 DONE 的作业不应被重复拉起。"""
        executor = allow_executor()
        ctx = make_ctx()
        token = tool_context.set(ctx)
        try:
            rsp = await background(tool_name="echo", args={})
            await executor.launch_pending(ctx, {"echo": make_tool("echo", "a")})
            await wait_job(ctx.jobs, rsp.job_id, JobStatus.DONE)
            await executor.launch_pending(ctx, {"echo": make_tool("echo", "b")})
            # 不被重复执行（DONE 保持，task 未变）
            assert ctx.jobs[rsp.job_id].result == "a"
        finally:
            tool_context.reset(token)


# ── 集成：Runner 两轮模型驱动（派发 → 取回）────────────────────────────────

@pytest.fixture
def provider():
    p = FakeProvider()
    FakeProvider.set_default(p)
    yield p
    FakeProvider.set_default(None)


class TestRunnerIntegration:
    async def test_two_turns_background_and_fetch(self, provider):
        """第一轮模型调 background 派发；后台跑完；第二轮模型调 job_result 取回。"""
        executor = allow_executor()
        runner = Runner(executor=executor)
        channel = FakeChannel()
        env = SessionEnv(
            channel=channel,
            kernel=PyKernel(),
            messages=InMemoryMessages(),
            jobs={},
        )

        def agent_tools():
            return [
                make_tool("slow", "slow-done", delay=0.05),
                Tool.from_function(background),
                Tool.from_function(job_result),
            ]

        agent = Agent(instruction="You are helpful.", tools=agent_tools)

        # 第一轮：模型调 background（工具执行完需再给一个文本响应结束本轮）
        provider.client.chat.completions.set_responses(
            FakeCompletion(FakeMessage(tool_calls=[
                tool_call("background", '{"tool_name": "slow", "args": {}}'),
            ])),
            FakeCompletion(FakeMessage(content="Started.")),
        )
        await runner.run(agent, "run it in background", env=env)

        # 派发后作业进入 env.jobs 并已被拉起，等它跑完
        assert len(env.jobs) == 1
        job_id = next(iter(env.jobs))
        await wait_job(env.jobs, job_id, JobStatus.DONE)
        assert env.jobs[job_id].result == "slow-done"

        # 第二轮：模型调 job_result 取回（结果一次性消费）
        provider.client.chat.completions.set_responses(
            FakeCompletion(FakeMessage(tool_calls=[
                tool_call("job_result", json.dumps({"job_id": job_id})),
            ])),
            FakeCompletion(FakeMessage(content="All done.")),
        )
        result = await runner.run(agent, "fetch the result", env=env)
        assert result.output == "All done."
        assert job_id not in env.jobs  # 取走即删

    async def test_async_second_turn_sees_running_then_done(self, provider):
        """两轮之间作业仍在跑：第二轮 job_result 先看到 running，随后完成。"""
        executor = allow_executor()
        runner = Runner(executor=executor)
        env = SessionEnv(
            channel=FakeChannel(),
            kernel=PyKernel(),
            messages=InMemoryMessages(),
            jobs={},
        )

        def agent_tools():
            return [
                make_tool("slow", "done-42", delay=0.1),
                Tool.from_function(background),
                Tool.from_function(job_result),
            ]

        agent = Agent(instruction="You are helpful.", tools=agent_tools)

        provider.client.chat.completions.set_responses(
            FakeCompletion(FakeMessage(tool_calls=[
                tool_call("background", '{"tool_name": "slow", "args": {}}'),
            ])),
            FakeCompletion(FakeMessage(content="Started.")),
        )
        await runner.run(agent, "start bg", env=env)
        job_id = next(iter(env.jobs))

        # 立即第二轮（后台可能还在跑）：返回 running
        provider.client.chat.completions.set_responses(
            FakeCompletion(FakeMessage(tool_calls=[
                tool_call("job_result", json.dumps({"job_id": job_id})),
            ])),
            FakeCompletion(FakeMessage(content="ok.")),
        )
        await runner.run(agent, "check now", env=env)

        # 作业仍在表里（running 不消费），等它完成后应可再次取回
        assert job_id in env.jobs
        await wait_job(env.jobs, job_id, JobStatus.DONE)
        assert env.jobs[job_id].result == "done-42"


# ── Session close 取消 ───────────────────────────────────────────────────────

class TestSessionCloseCancels:
    async def test_session_close_cancels_pending_jobs(self, tmp_path):
        executor = allow_executor()
        runner = Runner(executor=executor)
        s = Session(
            session_id="jobs-1",
            agent_factory=lambda name=None: Agent(instruction="You are helpful."),
            runner=runner,
            db_path=str(tmp_path / "s.db"),
        )
        # 派发一个永不完成的后台作业
        async def never(args):
            await asyncio.Event().wait()

        ctx = ToolContext(jobs=s.jobs)
        token = tool_context.set(ctx)
        try:
            rsp = await background(tool_name="never", args={})
            await executor.launch_pending(ctx, {
                "never": Tool(name="never", description="never",
                              parameters={"type": "object", "properties": {}},
                              fn=never),
            })
            task = s.jobs[rsp.job_id].task
            assert not task.done()
        finally:
            tool_context.reset(token)

        await s.close()
        assert task.cancelled()
        assert s.jobs == {}
