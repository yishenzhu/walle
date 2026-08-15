"""τ-bench 公开基准评测入口。

用法：
  PYTHONPATH=.. .venv/bin/python -m walle.eval.bench.run_tau --limit 5    # 小规模验证
  PYTHONPATH=.. .venv/bin/python -m walle.eval.bench.run_tau             # 全量 retail test
  PYTHONPATH=.. .venv/bin/python -m walle.eval.bench.run_tau --env airline --split dev

读取 .env 的 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL（被测 agent 模型）；
user simulator 默认同模型，可用 --user-model 指定更强的模型。
"""

import argparse
import asyncio
import os
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

from ...conf import ApprovalConfig, ApprovalDecision, TimeoutConfig, ToolConfig
from ...infra import OpenAIProvider, PyKernel
from ...core.agent import Agent
from ...core.runner import Runner, RunOptions, SessionEnv
from ...messages import InMemoryMessages

from ..harness import RecordingExecutor, TaskResult, TrackedProvider
from ..metrics import Pricing, aggregate
from ..report import (
    load_results_detail,
    load_results_json,
    now_iso,
    render_report,
    save_results_detail,
    save_results_json,
    write_results_csv,
)

from .tau_adapter import (
    DEFAULT_MAX_TURNS,
    WalleUserSimulationEnv,
    build_tau_tools,
    make_task_spec,
)
from tau_bench.types import Action

DEFAULT_OUTDIR = Path(__file__).resolve().parent.parent / "report" / "tau"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="τ-bench 公开基准评测")
    p.add_argument("--env", default="retail", choices=["retail", "airline"])
    p.add_argument("--split", default="test", choices=["train", "test", "dev"])
    p.add_argument("--start", type=int, default=0, help="起始用例下标")
    p.add_argument("--limit", type=int, default=-1, help="跑多少个用例（-1 = 全部）")
    p.add_argument("--user-model", default=None, help="user simulator 模型（默认同 .env 模型）")
    p.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    p.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    p.add_argument("--price-prompt", type=float, default=0.0, help="输入单价 USD/M token")
    p.add_argument("--price-completion", type=float, default=0.0, help="输出单价 USD/M token")
    p.add_argument("--resume", action="store_true", help="从上次中断处续跑（读已有 detail）")
    p.add_argument("--concurrency", type=int, default=1,
                   help="并发用例数（线程池；每用例独立 env/provider，默认 1）")
    return p.parse_args(argv)


def load_done_indices(outdir: Path) -> set[int]:
    """读已有 results_detail.json，返回已完成的用例下标。"""
    d = load_results_detail(outdir / "results_detail.json")
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
    tools_src,
    state: dict,
    max_turns: int,
) -> TaskResult:
    """跑单个 τ-bench 用例：env.reset → Walle Runner 驱动 → 取 reward。

    τ-bench 协议：agent 的回复 = respond 动作。模型主动调 respond 工具时正常流转；
    若模型输出纯文本（未调 respond），Runner 结束，外层把该文本兜底为 respond
    （与官方 tool-calling agent 语义一致），拿到用户回复后继续，直到对话 done
    或总轮次超限。
    """
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
    walle_env = SessionEnv(
        provider=tracked,
        channel=None,
        kernel=PyKernel(),
        messages=InMemoryMessages(),
        jobs={},
    )
    agent = Agent(
        name="tau",
        description=f"tau-bench {env_name(env)} agent",
        instruction=env.wiki
        + (
            "\n\nIMPORTANT: You MUST use the 'respond' tool to send any message to the user. "
            "Never reply with plain text — always call the respond tool with your message as "
            "the 'content' argument. Use the other tools to look up and modify user data. "
            "Continue the conversation until the user's request is fully handled."
        ),
        temperature=0.0,
        tools=tools_src,
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
    except asyncio.TimeoutError:
        error = "timeout after 900s"
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    finally:
        await walle_env.kernel.close()
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
        task=make_task_spec(task_index, env.tasks[task_index].instruction[:120], env_name(env)),
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


def env_name(env) -> str:
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
        """每用例独立环境（并发安全）：env + 工具集 + state + provider。

        构造期用 human strategy：litellm 的 LLM 策略构造时会立即发起 API 调用，
        构造后替换为走同一网关的 WalleUserSimulationEnv。
        """
        e = MockRetailDomainEnv(
            user_strategy="human",
            user_model=user_model,
            user_provider="openai",
            task_split=args.split,
        )
        e.user = WalleUserSimulationEnv(api_key=api_key, base_url=base_url, model=user_model)
        tools_i, state_i = build_tau_tools(e)
        provider_i = OpenAIProvider(api_key=api_key, base_url=base_url, model=model)
        return e, lambda: tools_i, state_i, provider_i

    # 探路 env：仅取任务列表 / 任务描述（并发跑时不共享）
    probe_env = build_env()[0]
    indices = list(range(args.start, len(probe_env.tasks)))
    if args.limit > 0:
        indices = indices[: args.limit]
    done = load_done_indices(args.outdir) if args.resume else set()
    indices = [idx for idx in indices if idx not in done]
    if done:
        print(f"resume: 跳过已完成的 {len(done)} 个用例，剩余 {len(indices)}")
    print(f"tau-bench env={args.env} split={args.split} tasks={len(indices)} "
          f"agent_model={model} user_model={user_model} "
          f"concurrency={args.concurrency}")

    pricing = Pricing(
        prompt_per_m=args.price_prompt, completion_per_m=args.price_completion
    )
    args.outdir.mkdir(parents=True, exist_ok=True)

    results: list[TaskResult] = []

    def restore_done() -> None:
        """续跑：把已完成用例的 TaskResult 从 detail json 恢复进 results。"""
        prev_detail = load_results_detail(args.outdir / "results_detail.json")
        if not prev_detail:
            return
        for row in prev_detail["results"]:
            try:
                idx = int(row["name"].rsplit("_", 1)[1])
            except (ValueError, KeyError):
                continue
            results.append(
                TaskResult(
                    task=make_task_spec(
                        idx, probe_env.tasks[idx].instruction[:120], env_name(probe_env)
                    ),
                    success=row["success"],
                    detail=row.get("detail", []),
                    turns=row.get("turns", 0),
                    llm_calls=row.get("llm_calls", 0),
                    prompt_tokens=row.get("prompt_tokens", 0),
                    completion_tokens=row.get("completion_tokens", 0),
                    tool_calls=row.get("tool_calls", []),
                    tool_errors=row.get("tool_errors", 0),
                    elapsed=row.get("elapsed", 0.0),
                    error=row.get("error"),
                    output=row.get("output"),
                    last_agent=row.get("last_agent"),
                )
            )

    if args.resume:
        restore_done()

    def write_artifacts(partial: list[TaskResult]) -> None:
        """增量写报告（每完成一个用例调用，中断最多丢当前用例）。"""
        meta = {
            "timestamp": now_iso(),
            "model": model,
            "user_model": user_model,
            "repeat": 1,
            "bench": f"tau_{args.env}_{args.split}",
        }
        report_md = render_report(partial, pricing, meta, previous=None)
        (args.outdir / "report.md").write_text(report_md, encoding="utf-8")
        write_results_csv(partial, pricing, args.outdir / "results.csv")
        save_results_json(partial, pricing, meta, args.outdir / "results.json")
        save_results_detail(partial, args.outdir / "results_detail.json")

    print_lock = threading.Lock()

    def run_one(idx: int) -> TaskResult:
        """线程内跑单个用例：独立 env/provider/kernel，互不共享。"""
        e, tools_src_i, state_i, provider_i = build_env()
        try:
            return asyncio.run(
                run_tau_case(e, idx, provider_i, tools_src_i, state_i, args.max_turns)
            )
        except Exception as exc:  # 线程级兜底：不因单用例异常中断整批
            return TaskResult(
                task=make_task_spec(idx, probe_env.tasks[idx].instruction[:120], env_name(probe_env)),
                success=False,
                detail=[f"thread error: {type(exc).__name__}: {exc}"],
                error=f"{type(exc).__name__}: {exc}",
                tool_calls=[],
            )

    done_count = 0
    if args.concurrency <= 1:
        for i, idx in enumerate(indices, 1):
            print(f"[{i}/{len(indices)}] task {idx} ...", end="", flush=True)
            res = run_one(idx)
            results.append(res)
            write_artifacts(results)
            mark = "PASS" if res.success else "FAIL"
            with print_lock:
                print(
                    f" {mark} turns={res.turns} tokens={res.tokens}"
                    f" tools={len(res.tool_calls)} {res.elapsed:.0f}s"
                    + (f"  {res.error}" if res.error else "")
                )
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {pool.submit(run_one, idx): idx for idx in indices}
            for fut in as_completed(futures):
                idx = futures[fut]
                res = fut.result()
                results.append(res)
                write_artifacts(results)
                done_count += 1
                mark = "PASS" if res.success else "FAIL"
                with print_lock:
                    print(
                        f"[{done_count}/{len(indices)}] task {idx} ... {mark}"
                        f" turns={res.turns} tokens={res.tokens}"
                        f" tools={len(res.tool_calls)} {res.elapsed:.0f}s"
                        + (f"  {res.error}" if res.error else "")
                    )

    # 最终报告（带趋势对比；续跑时 detail 已是最新，跳过重复写）
    if not args.resume:
        meta = {
            "timestamp": now_iso(),
            "model": model,
            "user_model": user_model,
            "repeat": 1,
            "bench": f"tau_{args.env}_{args.split}",
        }
        prev = load_results_json(args.outdir / "results.json")
        report_md = render_report(results, pricing, meta, previous=prev)
        (args.outdir / "report.md").write_text(report_md, encoding="utf-8")
        write_results_csv(results, pricing, args.outdir / "results.csv")
        save_results_json(results, pricing, meta, args.outdir / "results.json")
        save_results_detail(results, args.outdir / "results_detail.json")

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
