"""τ-bench 公开基准评测入口。"""

import argparse
import asyncio
import os
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from tau_bench.types import Action

from ...conf import ApprovalConfig, ApprovalDecision, TimeoutConfig, ToolConfig
from ...core import EventBus, SessionToolTable
from ...core.agent import Agent
from ...core.runner import Runner, RunOptions, SessionContext
from ...infra import OpenAIProvider
from ...messages import InMemoryMessages
from ..harness import RecordingExecutor, TaskResult, TrackedProvider
from ..metrics import Pricing, aggregate
from ..report import load_results, save_reports, utc_timestamp
from .tau_adapter import (
    DEFAULT_MAX_TURNS,
    WalleUserSimulationEnv,
    build_task_spec,
    build_tau_tools,
)

DEFAULT_OUTDIR = Path(__file__).resolve().parent.parent / "report" / "tau"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="τ-bench 公开基准评测")
    p.add_argument("--env", default="retail", choices=["retail", "airline"])
    p.add_argument("--split", default="test", choices=["train", "test", "dev"])
    p.add_argument("--start", type=int, default=0, help="起始用例下标")
    p.add_argument("--limit", type=int, default=-1, help="跑多少个用例（-1 = 全部）")
    p.add_argument(
        "--user-model", default=None, help="user simulator 模型（默认同 .env 模型）"
    )
    p.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    p.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    p.add_argument(
        "--price-prompt", type=float, default=0.0, help="输入单价 USD/M token"
    )
    p.add_argument(
        "--price-completion", type=float, default=0.0, help="输出单价 USD/M token"
    )
    p.add_argument(
        "--resume", action="store_true", help="从上次中断处续跑（读已有 detail）"
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="并发用例数（线程池；每用例独立 env/provider，默认 1）",
    )
    return p.parse_args(argv)


def load_completed_indices(outdir: Path) -> set[int]:
    """读已有 results_detail.json，返回已完成的用例下标。"""
    d = load_results(outdir / "results_detail.json")
    if not d:
        return set()
    done: set[int] = set()
    for r in d["results"]:
        try:
            done.add(int(r["name"].rsplit("_", 1)[1]))
        except (ValueError, KeyError):
            pass
    return done


async def run_tau_case(
    env,
    task_index: int,
    agent_provider: OpenAIProvider,
    tool_source,
    state: dict,
    max_turns: int,
) -> TaskResult:
    """跑单个 τ-bench 用例：env.reset → Walle Runner 驱动 → 取 reward。"""
    observation = env.reset(task_index).observation
    state["reward"] = None
    state["stopped"] = False

    tracked = TrackedProvider(agent_provider)
    executor = RecordingExecutor(
        ToolConfig(
            approval=ApprovalConfig(rules=[], default=ApprovalDecision.ALLOW),
            timeout=TimeoutConfig(default=120.0),
        )
    )
    bus = EventBus()
    tool_table = SessionToolTable()
    tool_table.add(*tool_source())
    walle_env = SessionContext(
        provider=tracked,
        channel=None,
        history=InMemoryMessages(),
        jobs={},
        tool_table=tool_table,
        bus=bus,
    )
    agent = Agent(
        name="tau",
        description=f"tau-bench {env_slug(env)} agent",
        instruction=env.wiki
        + (
            "\n\nIMPORTANT: You MUST use the 'respond' tool to send any message to the user. "
            "Never reply with plain text — always call the respond tool with your message as "
            "the 'content' argument. Use the other tools to look up and modify user data. "
            "Continue the conversation until the user's request is fully handled."
        ),
        temperature=0.0,
    )
    runner = Runner(executor=executor)

    start = time.monotonic()
    error: str | None = None
    spent = 0
    output: str | None = None
    try:
        while spent < max_turns and state["reward"] is None:
            remaining = max_turns - spent
            result = await asyncio.wait_for(
                runner.run(
                    agent,
                    observation,
                    env=walle_env,
                    options=RunOptions(max_turns=remaining),
                ),
                timeout=900.0,
            )
            spent += result.completed_turns
            if state["reward"] is not None:
                break  # 对话已 done（reward 已算）
            if result.output is not None:
                # 模型纯文本输出 → 兜底为 respond（官方协议语义），user simulator 继续
                output = str(result.output)
                resp = env.step(Action(name="respond", kwargs={"content": output}))
                if resp.done:
                    state["reward"] = resp.reward
                    state["stopped"] = True
                    break
                observation = resp.observation
            else:
                # 无文本输出：只能是轮次耗尽（模型一直调工具）
                break
    except TimeoutError:
        error = "timeout after 900s"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        # 线程内必须显式关闭 client，否则 httpx 连接在事件循环关闭后
        # 才尝试 aclose，报 "Event loop is closed"
        await agent_provider.close()
    elapsed = time.monotonic() - start

    reward = state["reward"]
    if error is not None:
        success, detail = False, [f"task error: {error}"]
    elif reward is None:
        success, detail = False, ["对话未结束（总轮次超限或引擎空转）"]
    else:
        success, detail = reward == 1.0, [f"tau reward={reward}"]

    return TaskResult(
        task=build_task_spec(
            task_index, env.tasks[task_index].instruction[:120], env_slug(env)
        ),
        success=success,
        detail=detail,
        turns=spent,
        llm_calls=tracked.calls,
        prompt_tokens=tracked.usage.prompt_tokens,
        completion_tokens=tracked.usage.completion_tokens,
        tool_calls=executor.calls,
        tool_errors=executor.tool_errors,
        elapsed=elapsed,
        error=error,
        output=output,
        last_agent=None,
    )


def env_slug(env) -> str:
    return type(env).__name__.replace("Mock", "").replace("DomainEnv", "").lower()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    model = os.getenv("OPENAI_MODEL")
    if not (api_key and base_url and model):
        print("缺少 .env 配置：OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL")
        return 1

    from tau_bench.envs.retail import MockRetailDomainEnv

    user_model = args.user_model or model

    def build_env() -> tuple:
        """每用例独立环境（并发安全）：env + 工具集 + state + provider。"""
        env = MockRetailDomainEnv(
            user_strategy="human",
            user_model=user_model,
            user_provider="openai",
            task_split=args.split,
        )
        env.user = WalleUserSimulationEnv(
            api_key=api_key, base_url=base_url, model=user_model
        )
        tool_source, state = build_tau_tools(env)
        provider = OpenAIProvider(api_key=api_key, base_url=base_url, model=model)
        return env, lambda: tool_source, state, provider

    # 探路 env：仅取任务列表 / 任务描述（并发跑时不共享）
    probe_env = build_env()[0]
    indices = list(range(args.start, len(probe_env.tasks)))
    if args.limit > 0:
        indices = indices[: args.limit]
    done = load_completed_indices(args.outdir) if args.resume else set()
    indices = [index for index in indices if index not in done]
    if done:
        print(f"resume: 跳过已完成的 {len(done)} 个用例，剩余 {len(indices)}")
    print(
        f"tau-bench env={args.env} split={args.split} tasks={len(indices)} "
        f"agent_model={model} user_model={user_model} "
        f"concurrency={args.concurrency}"
    )

    pricing = Pricing(
        prompt_per_m=args.price_prompt, completion_per_m=args.price_completion
    )
    args.outdir.mkdir(parents=True, exist_ok=True)

    results: list[TaskResult] = []

    def restore_done() -> None:
        """续跑：把已完成用例的 TaskResult 从 detail json 恢复进 results。"""
        prev_detail = load_results(args.outdir / "results_detail.json")
        if not prev_detail:
            return
        for row in prev_detail["results"]:
            try:
                index = int(row["name"].rsplit("_", 1)[1])
            except (ValueError, KeyError):
                continue
            task = build_task_spec(
                index, probe_env.tasks[index].instruction[:120], env_slug(probe_env)
            )
            results.append(TaskResult.from_row(task, row))

    if args.resume:
        restore_done()

    def report_meta() -> dict:
        return {
            "timestamp": utc_timestamp(),
            "model": model,
            "user_model": user_model,
            "repeat": 1,
            "bench": f"tau_{args.env}_{args.split}",
        }

    def write_artifacts(partial: list[TaskResult]) -> None:
        """增量写报告（每完成一个用例调用，中断最多丢当前用例）。"""
        save_reports(partial, pricing, report_meta(), args.outdir)

    print_lock = threading.Lock()

    def run_one(index: int) -> TaskResult:
        """线程内跑单个用例：独立 env/provider，互不共享。"""
        env, tool_source, state, provider = build_env()
        try:
            return asyncio.run(
                run_tau_case(env, index, provider, tool_source, state, args.max_turns)
            )
        except Exception as exc:  # 线程级兜底：不因单用例异常中断整批
            return TaskResult(
                task=build_task_spec(
                    index, probe_env.tasks[index].instruction[:120], env_slug(probe_env)
                ),
                success=False,
                detail=[f"thread error: {type(exc).__name__}: {exc}"],
                error=f"{type(exc).__name__}: {exc}",
                tool_calls=[],
            )

    def record_result(result: TaskResult) -> None:
        results.append(result)
        write_artifacts(results)

    def summary(result: TaskResult) -> str:
        mark = "PASS" if result.success else "FAIL"
        return (
            f"{mark} turns={result.turns} tokens={result.tokens}"
            f" tools={len(result.tool_calls)} {result.elapsed:.0f}s"
            + (f"  {result.error}" if result.error else "")
        )

    done_count = 0
    if args.concurrency <= 1:
        for i, index in enumerate(indices, 1):
            print(f"[{i}/{len(indices)}] task {index} ...", end="", flush=True)
            result = run_one(index)
            record_result(result)
            with print_lock:
                print(f" {summary(result)}")
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {pool.submit(run_one, index): index for index in indices}
            for future in as_completed(futures):
                index = futures[future]
                result = future.result()
                record_result(result)
                done_count += 1
                with print_lock:
                    print(
                        f"[{done_count}/{len(indices)}] task {index} ... "
                        f"{summary(result)}"
                    )

    # 最终报告（带趋势对比；续跑时 detail 已是最新，跳过重复写）
    if not args.resume:
        prev = load_results(args.outdir / "results.json")
        save_reports(
            results, pricing, report_meta(), args.outdir, previous=prev
        )

    overall = aggregate(results, pricing)
    print("\n=== 总览 ===")
    print(
        f"reward==1: {overall.passed}/{overall.n} ({overall.success_rate * 100:.1f}%)"
        f"  平均轮次: {overall.avg_turns}  token/任务: {overall.avg_tokens:,.0f}"
    )
    print(f"报告: {args.outdir}/report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
