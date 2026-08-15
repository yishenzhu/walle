"""报告生成：report.md + results.csv + results.json（趋势）+ results_detail.json（重渲染）。

单元格内容统一转义 `|`，避免 markdown 表格结构被内容破坏。
"""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .harness import TaskResult
from .metrics import GroupStats, Pricing, aggregate, by_domain, task_row


def _cell(s: Any) -> str:
    """表格单元格转义：管道符转义、换行压平、去首尾空白。"""
    return str(s).replace("|", "\\|").replace("\n", " ").strip()


def _pct(stats: GroupStats) -> str:
    return f"{stats.passed}/{stats.n} ({stats.success_rate * 100:.1f}%)"


def _fmt_cost(v: float) -> str:
    return f"${v:.4f}" if v else "-"


def _bar(rate: float, width: int = 12) -> str:
    """ASCII 进度条：█ 实心 / ░ 空心。"""
    filled = round(rate * width)
    return "█" * filled + "░" * (width - filled)


def _compact_detail(items: list[str]) -> str:
    """把评分明细压缩成短标签：'numeric 8.0±0.01: PASS' → 'numeric PASS'。

    失败项保留完整描述（调试需要）。多项用 · 连接（避免引入表格分隔符）。
    """
    parts = []
    for it in items:
        if ": PASS" in it:
            parts.append(it.split(": ", 1)[0] + " PASS")
        elif ": FAIL" in it or "no match" in it or "missing" in it or "actual=" in it:
            parts.append(it)
        else:
            parts.append(it)
    return " · ".join(parts) or "-"


def _trace_arrow(r: TaskResult) -> str:
    names = [c["name"] for c in r.tool_calls]
    return " → ".join(names) if names else "—"


def render_report(
    results: list[TaskResult],
    pricing: Pricing,
    meta: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> str:
    """渲染 markdown 报告。previous 为上次 results.json，用于趋势对比。"""
    overall = aggregate(results, pricing)
    domains = by_domain(results, pricing)

    lines: list[str] = []
    lines.append("# Walle 评测报告 🤖\n")
    lines.append(
        f"- **时间**: {meta['timestamp']}　·　**模型**: `{meta['model']}`　·　"
        f"**重复**: {meta['repeat']}　·　"
        f"**单价**: ${pricing.prompt_per_m}/M in · ${pricing.completion_per_m}/M out\n"
    )

    if previous:
        prev_n, prev_rate = previous.get("n", 0), previous.get("success_rate", 0.0)
        prev_tokens = previous.get("avg_tokens", 0.0)
        if prev_n == overall.n:
            delta_rate = overall.success_rate - prev_rate
            delta_tokens = overall.avg_tokens - prev_tokens
            lines.append(
                f"> 🔁 对比上次（n={prev_n}）：成功率 "
                f"{delta_rate * 100:+.1f}pp · 平均 token/任务 {delta_tokens:+.0f}\n"
            )
        else:
            lines.append(
                f"> 🔁 对比上次：任务数不同（上次 n={prev_n}，本次 n={overall.n}），跳过趋势对比\n"
            )

    # 总体：单行指标表
    total_calls = sum(len(r.tool_calls) for r in results)
    lines.append("## 📊 总体\n")
    lines.append(
        "| 成功率 | 平均轮次 | token/任务 | 耗时/任务 | 工具调用 | 成本/任务 |\n"
        "|---|---|---|---|---|---|"
    )
    lines.append(
        f"| **{_pct(overall)}** | {overall.avg_turns} | {overall.avg_tokens:,.0f} | "
        f"{overall.avg_elapsed}s | {total_calls}（错误 {overall.tool_errors}） | "
        f"{_fmt_cost(overall.avg_cost)} |"
    )
    lines.append("")

    # 分域：带进度条
    lines.append("## 🗂️ 分域\n")
    lines.append(
        "| 域 | 成功率 | 进度 | 平均轮次 | token/任务 | 成本/任务 |\n"
        "|---|---|---|---|---|---|"
    )
    for d, s in domains.items():
        lines.append(
            f"| {_cell(d)} | {_pct(s)} | `{_bar(s.success_rate)}` | {s.avg_turns} | "
            f"{s.avg_tokens:,.0f} | {_fmt_cost(s.avg_cost)} |"
        )
    lines.append("")

    # 明细：压缩判定 + 工具序列列
    lines.append("## 📋 明细\n")
    lines.append(
        "| 任务 | 域 | 结果 | 轮次 | token | 耗时(s) | 工具序列 | 判定 |\n"
        "|---|---|---|---|---|---|---|---|"
    )
    for r in sorted(results, key=lambda r: (r.task.domain, r.task.name)):
        lines.append(
            f"| {_cell(r.task.name)} | {_cell(r.task.domain)} | "
            f"{'✅' if r.success else '❌'} | {r.turns} | {r.tokens:,} | "
            f"{r.elapsed:.1f} | {_cell(_trace_arrow(r))} | "
            f"{_cell(_compact_detail(r.detail) or (r.error or ''))} |"
        )
    lines.append("")

    # 附录：失败详情
    failed = [r for r in results if not r.success]
    if failed:
        lines.append("## ❌ 失败详情\n")
        for r in failed:
            lines.append(f"### {r.task.name} · {r.task.domain}\n")
            lines.append(f"**prompt**\n\n```\n{r.task.prompt}\n```\n")
            lines.append(
                f"**期望**: `{r.task.ground_truth.model_dump_json()}` 　·　 "
                f"**实际工具序列**: `{_trace_arrow(r)}`\n"
            )
            out = r.output
            if out:
                out = out if len(out) <= 400 else out[:400] + "..."
                lines.append(f"**最终输出**\n\n```\n{out}\n```\n")
            if r.error:
                lines.append(f"**任务错误**: `{_cell(r.error)}`\n")
            lines.append("---\n")

    return "\n".join(lines)


def write_results_csv(results: list[TaskResult], pricing: Pricing, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(task_row(results[0], pricing)))
        writer.writeheader()
        for r in results:
            writer.writerow(task_row(r, pricing))


def save_results_json(
    results: list[TaskResult], pricing: Pricing, meta: dict[str, Any], path: Path
) -> None:
    """保存聚合结果（趋势对比用），不含逐任务明细。"""
    overall = aggregate(results, pricing)
    data = {
        "timestamp": meta["timestamp"],
        "model": meta["model"],
        "n": overall.n,
        "passed": overall.passed,
        "success_rate": overall.success_rate,
        "avg_turns": overall.avg_turns,
        "avg_tokens": overall.avg_tokens,
        "avg_cost": overall.avg_cost,
        "domains": {
            d: {
                "n": s.n,
                "passed": s.passed,
                "success_rate": s.success_rate,
                "avg_turns": s.avg_turns,
                "avg_tokens": s.avg_tokens,
            }
            for d, s in by_domain(results, pricing).items()
        },
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _sanitize(v: Any) -> Any:
    """JSON 序列化前清洗：工具结果可能是任意对象（Handoff / pydantic 模型）。"""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (list, tuple)):
        return [_sanitize(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _sanitize(x) for k, x in v.items()}
    return str(v)


def save_results_detail(results: list[TaskResult], path: Path) -> None:
    """保存逐任务完整明细（重渲染 / 深度分析用）。"""
    data = {
        "results": [
            {
                "name": r.task.name,
                "domain": r.task.domain,
                "success": r.success,
                "detail": r.detail,
                "turns": r.turns,
                "llm_calls": r.llm_calls,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "tool_calls": _sanitize(r.tool_calls),
                "tool_errors": r.tool_errors,
                "elapsed": r.elapsed,
                "error": r.error,
                "output": r.output,
                "last_agent": r.last_agent,
            }
            for r in results
        ]
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_results_detail(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_results_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
