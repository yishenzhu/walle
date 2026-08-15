"""评分器：按 ground_truth 判定任务输出与工具调用序列。

输出判定基于最终回复文本；工具序列为顺序敏感子序列匹配
（期望的调用必须按顺序出现，允许穿插额外调用，如报错重试）。
"""

import re
from typing import Any

from .spec import GroundTruth


def normalize(text: str) -> str:
    """归一化：折叠空白，便于 exact 比较。"""
    return " ".join(str(text).split()).strip()


def is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    """needle 是否为 haystack 的顺序子序列。"""
    it = iter(haystack)
    return all(x in it for x in needle)


def _grade_output(gt: GroundTruth, text: str) -> tuple[bool, str]:
    if gt.type == "exact":
        ok = normalize(text) == normalize(gt.value)
        return ok, f"exact: {'PASS' if ok else f'expected {gt.value!r}'}"
    if gt.type == "contains":
        ok = gt.value in text
        return ok, f"contains {gt.value!r}: {'PASS' if ok else 'missing'}"
    if gt.type == "numeric":
        expected = float(gt.value)
        numbers = [float(m) for m in re.findall(r"-?\d+(?:\.\d+)?", text)]
        hit = any(abs(n - expected) <= gt.tolerance for n in numbers)
        return hit, (
            f"numeric {expected}±{gt.tolerance}: "
            f"{'PASS' if hit else f'no match in {numbers[:8]}'}"
        )
    if gt.type == "regex":
        ok = re.search(gt.value, text) is not None
        return ok, f"regex {gt.value!r}: {'PASS' if ok else 'no match'}"
    return False, f"unknown type: {gt.type}"


def grade(
    gt: GroundTruth,
    output: Any | None,
    tool_names: list[str],
) -> tuple[bool, list[str]]:
    """综合评分：输出判定 + 工具序列。返回 (通过, 明细列表)。"""
    if output is None:
        return False, ["no output (max turns or error)"]
    ok, detail = _grade_output(gt, str(output))
    details = [detail]
    if gt.tool_trace:
        sub = is_subsequence(gt.tool_trace, tool_names)
        ok = ok and sub
        details.append(
            f"trace {gt.tool_trace}: "
            f"{'PASS' if sub else f'actual={tool_names}'}"
        )
    return ok, details
