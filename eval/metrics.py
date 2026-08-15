"""指标聚合：成功率 / 平均轮次 / token / 成本（按域 + 总体）。"""

from dataclasses import dataclass, field
from typing import Any

from .harness import TaskResult


@dataclass
class Pricing:
    """模型单价（USD / 百万 token）。默认 0，用 --price-* 传入真实值。"""

    prompt_per_m: float = 0.0
    completion_per_m: float = 0.0

    def cost(self, r: TaskResult) -> float:
        return (
            r.prompt_tokens / 1e6 * self.prompt_per_m
            + r.completion_tokens / 1e6 * self.completion_per_m
        )


@dataclass
class GroupStats:
    """一组结果（总体或某个域）的聚合。"""

    label: str
    n: int
    passed: int
    avg_turns: float = 0.0
    avg_tokens: float = 0.0
    avg_cost: float = 0.0
    avg_elapsed: float = 0.0
    tool_errors: int = 0
    errors: list[str] = field(default_factory=list)  # 任务级失败摘要

    @property
    def success_rate(self) -> float:
        return self.passed / self.n if self.n else 0.0


def aggregate(
    results: list[TaskResult], pricing: Pricing, label: str = "overall"
) -> GroupStats:
    n = len(results)
    passed = sum(1 for r in results if r.success)
    if n:
        avg_turns = sum(r.turns for r in results) / n
        avg_tokens = sum(r.tokens for r in results) / n
        avg_cost = sum(pricing.cost(r) for r in results) / n
        avg_elapsed = sum(r.elapsed for r in results) / n
    else:
        avg_turns = avg_tokens = avg_cost = avg_elapsed = 0.0
    return GroupStats(
        label=label,
        n=n,
        passed=passed,
        avg_turns=round(avg_turns, 2),
        avg_tokens=round(avg_tokens, 1),
        avg_cost=round(avg_cost, 4),
        avg_elapsed=round(avg_elapsed, 1),
        tool_errors=sum(r.tool_errors for r in results),
        errors=[r.error for r in results if r.error],
    )


def by_domain(results: list[TaskResult], pricing: Pricing) -> dict[str, GroupStats]:
    domains: dict[str, list[TaskResult]] = {}
    for r in results:
        domains.setdefault(r.task.domain, []).append(r)
    return {
        d: aggregate(rs, pricing, label=d) for d, rs in sorted(domains.items())
    }


def task_row(r: TaskResult, pricing: Pricing) -> dict[str, Any]:
    """单任务一行数据（CSV / 报告共用）。"""
    return {
        "name": r.task.name,
        "domain": r.task.domain,
        "pass": "PASS" if r.success else "FAIL",
        "turns": r.turns,
        "llm_calls": r.llm_calls,
        "prompt_tokens": r.prompt_tokens,
        "completion_tokens": r.completion_tokens,
        "total_tokens": r.tokens,
        "cost_usd": round(pricing.cost(r), 6),
        "tool_calls": len(r.tool_calls),
        "tool_errors": r.tool_errors,
        "trace": "->".join(c["name"] for c in r.tool_calls),
        "elapsed_s": round(r.elapsed, 1),
        "detail": " | ".join(r.detail),
        "error": r.error or "",
    }
