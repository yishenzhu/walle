"""评测任务定义：YAML → TaskSpec 的加载与校验。

任务文件位于 eval/tasks/*.yaml，字段见 TaskSpec。评分规则见 ground_truth：
- type: exact / contains / numeric / regex（对最终输出判定）
- tool_trace: 期望的工具调用序列（顺序敏感的子序列匹配，允许穿插额外调用）
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

EVAL_ROOT = Path(__file__).resolve().parent
TASKS_DIR = EVAL_ROOT / "tasks"
DATA_DIR = EVAL_ROOT / "data"


class GroundTruth(BaseModel):
    """评分规则：type 判定最终输出；tool_trace 判定工具调用序列。"""

    type: Literal["exact", "contains", "numeric", "regex"]
    value: str
    tolerance: float = 0.01  # numeric 用：|实际值 - 期望值| <= tolerance 即通过
    tool_trace: list[str] = Field(default_factory=list)


class AgentSpec(BaseModel):
    """多智能体任务中的单个 Agent 定义（handoff 域用）。"""

    name: str
    description: str | None = None
    instruction: str | None = None
    tools: list[str] = Field(default_factory=list)  # 该 agent 的工具 allowlist
    handoffs: list[str] = Field(default_factory=list)  # 可移交的目标 agent 名


class TaskSpec(BaseModel):
    name: str
    domain: str
    prompt: str
    agent: str = "default"  # 非 handoff 任务：加载哪个 frontmatter agent
    agents: list[AgentSpec] = Field(default_factory=list)  # handoff 任务：agents[0] 为入口
    tools: list[str] = Field(default_factory=lambda: ["*"])  # 入口 agent 工具 allowlist
    max_turns: int = 8
    timeout: float = 300.0  # 单任务墙钟超时（秒）
    temperature: float | None = 0.0  # 评测默认 0 保证可复现
    ground_truth: GroundTruth

    @model_validator(mode="after")
    def _check_handoffs(self) -> "TaskSpec":
        """校验：tools 中出现 transfer_to_<x> 的 agent 必须声明对应的 handoffs 条目。"""
        names = {a.name for a in self.agents}
        for a in self.agents:
            for t in a.tools:
                if t.startswith("transfer_to_"):
                    target = t[len("transfer_to_") :]
                    if target not in a.handoffs:
                        raise ValueError(
                            f"agent '{a.name}' tools 含 {t} 但 handoffs 缺少 '{target}'"
                        )
                    if target not in names:
                        raise ValueError(f"handoff 目标 '{target}' 未在 agents 中定义")
        return self


def load_tasks(tasks_dir: Path = TASKS_DIR) -> list[TaskSpec]:
    """加载全部任务 YAML，按文件名排序。"""
    tasks: list[TaskSpec] = []
    for p in sorted(tasks_dir.glob("*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        task = TaskSpec.model_validate(data)
        if task.name != p.stem:
            raise ValueError(f"task name '{task.name}' != file name '{p.stem}'")
        tasks.append(task)
    return tasks
