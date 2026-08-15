"""评测入口：PYTHONPATH=.. python -m walle.eval.run [选项]

示例：
  PYTHONPATH=.. python -m walle.eval.run --smoke                    # 快速冒烟（1 个任务）
  PYTHONPATH=.. python -m walle.eval.run                            # 全量 20 任务
  PYTHONPATH=.. python -m walle.eval.run --domain codeact           # 只跑某域
  PYTHONPATH=.. python -m walle.eval.run --task 'bash*'             # glob 过滤任务名
  PYTHONPATH=.. python -m walle.eval.run --repeat 3                 # 每任务重复 3 次（报告均值）
  PYTHONPATH=.. python -m walle.eval.run --price-prompt 0.07 --price-completion 0.27
  PYTHONPATH=.. python -m walle.eval.run --render-only              # 不重跑 LLM，重渲染上次报告

读取 .env 的 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL（--render-only 不需要）。
"""

import argparse
import asyncio
import fnmatch
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .harness import OUT_DIR, TaskResult, build_tool_system, run_task
from .metrics import Pricing, aggregate
from .report import (
    load_results_detail,
    load_results_json,
    now_iso,
    render_report,
    save_results_detail,
    save_results_json,
    write_results_csv,
)
from .spec import TASKS_DIR, load_tasks

DEFAULT_OUTDIR = Path(__file__).resolve().parent / "report"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Walle 评测套件")
    p.add_argument("--domain", help="只跑指定域")
    p.add_argument("--task", help="按 glob 过滤任务名")
    p.add_argument("--repeat", type=int, default=1, help="每任务重复次数（默认 1）")
    p.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR, help="报告输出目录")
    p.add_argument("--price-prompt", type=float, default=0.0, help="输入单价 USD/M token")
    p.add_argument("--price-completion", type=float, default=0.0, help="输出单价 USD/M token")
    p.add_argument("--smoke", action="store_true", help="冒烟：只跑第一个任务")
    p.add_argument("--render-only", action="store_true", help="重渲染上次报告（不调 LLM）")
    p.add_argument("--retry", type=int, default=2, help="任务级失败（provider 超时等）的最大尝试次数")
    return p.parse_args(argv)


def pick_tasks(args: argparse.Namespace):
    tasks = load_tasks(TASKS_DIR)
    if args.smoke:
        return tasks[:1]
    if args.domain:
        tasks = [t for t in tasks if t.domain == args.domain]
    if args.task:
        tasks = [t for t in tasks if fnmatch.fnmatch(t.name, args.task)]
    if not tasks:
        print(f"no tasks matched (domain={args.domain}, task={args.task})")
        sys.exit(1)
    return tasks


def render_only(args: argparse.Namespace) -> int:
    """不调 LLM：从 results_detail.json 重建 TaskResult 并重渲染报告。"""
    detail = load_results_detail(args.outdir / "results_detail.json")
    if detail is None:
        print(f"缺少 {args.outdir / 'results_detail.json'}，先跑一次评测")
        return 1
    tasks = {t.name: t for t in load_tasks()}
    results: list[TaskResult] = []
    for row in detail["results"]:
        task = tasks.get(row["name"])
        if task is None:
            print(f"跳过未知任务: {row['name']}（任务定义可能已删除）")
            continue
        results.append(
            TaskResult(
                task=task,
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
    pricing = Pricing(
        prompt_per_m=args.price_prompt, completion_per_m=args.price_completion
    )
    meta = {"timestamp": now_iso(), "model": "re-render", "repeat": 1}
    prev = load_results_json(args.outdir / "results.json")
    report_md = render_report(results, pricing, meta, previous=prev)
    (args.outdir / "report.md").write_text(report_md, encoding="utf-8")
    write_results_csv(results, pricing, args.outdir / "results.csv")
    print(f"重渲染完成: {args.outdir}/report.md（{len(results)} 条结果，未调 LLM）")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.render_only:
        return render_only(args)
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    model = os.getenv("OPENAI_MODEL")
    if not (api_key and base_url and model):
        print("缺少 .env 配置：OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL")
        return 1

    from ..infra import OpenAIProvider

    provider = OpenAIProvider(api_key=api_key, base_url=base_url, model=model)
    pricing = Pricing(
        prompt_per_m=args.price_prompt, completion_per_m=args.price_completion
    )
    tasks = pick_tasks(args)
    tools_src = build_tool_system()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    args.outdir.mkdir(parents=True, exist_ok=True)

    print(f"model={model}  tasks={len(tasks)}  repeat={args.repeat}")
    results = []
    for i, task in enumerate(tasks, 1):
        for rep in range(args.repeat):
            tag = f"[{i}/{len(tasks)}]"
            if args.repeat > 1:
                tag += f" (rep {rep + 1})"
            print(f"{tag} {task.name} ...", end="", flush=True)
            res = None
            for attempt in range(max(1, args.retry)):
                res = asyncio.run(run_task(task, provider, tools_src))
                if res.error is None:
                    break
                if attempt + 1 < args.retry:
                    print(f" retry{attempt + 2}...", end="", flush=True)
            assert res is not None
            results.append(res)
            mark = "PASS" if res.success else "FAIL"
            print(
                f" {mark} turns={res.turns} tokens={res.tokens}"
                f" tools={len(res.tool_calls)}/{res.tool_errors}"
                f" {res.elapsed:.1f}s"
                + (f"  {res.error}" if res.error else "")
            )

    # 全部任务级失败（provider 故障）时拒绝覆盖已有报告，避免好数据被冲掉
    if results and all(r.error is not None for r in results):
        print("\n全部任务均失败（疑似 provider 故障），未覆盖已有报告")
        print("样例错误:", results[0].error)
        return 1

    meta = {"timestamp": now_iso(), "model": model, "repeat": args.repeat}
    prev = load_results_json(args.outdir / "results.json")
    report_md = render_report(results, pricing, meta, previous=prev)
    (args.outdir / "report.md").write_text(report_md, encoding="utf-8")
    write_results_csv(results, pricing, args.outdir / "results.csv")
    save_results_json(results, pricing, meta, args.outdir / "results.json")
    save_results_detail(results, args.outdir / "results_detail.json")

    overall = aggregate(results, pricing)
    print("\n=== 总览 ===")
    print(
        f"成功率: {overall.passed}/{overall.n} ({overall.success_rate * 100:.1f}%)"
        f"  平均轮次: {overall.avg_turns}  token/任务: {overall.avg_tokens:,.0f}"
        f"  耗时/任务: {overall.avg_elapsed}s"
    )
    print(f"报告: {args.outdir}/report.md  CSV: {args.outdir}/results.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
