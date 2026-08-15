"""无头评测执行器：真实 LLM + 真实内置工具，NullChannel（无人工交互）。

与生产路径的差异（有意为之）：
- channel 为 None：无 notify / 审批（审批开销单独做实验，不混入能力评测）
- 审批策略 allow-all：与任务域无关的变量全部固定
- define_tool 持久化隔离到 eval/.agent-tools/，不污染 .agent/tools/
- 不加载 MCP / Skill：评测只覆盖核心引擎 + 内置工具（MCP 依赖外部服务）
- temperature 固定 0（可复现）
"""

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core import Agent, Handoff
from ..core.agent import ToolFilter
from ..core.executor import ToolExecutor
from ..core.runner import Runner, RunOptions, SessionEnv
from ..conf import ApprovalConfig, ApprovalDecision, TimeoutConfig, ToolConfig
from ..infra import OpenAIProvider, PyKernel
from ..messages import InMemoryMessages
from ..schemas import Usage
from ..tools import Tool
from ..tools.builtin import background, bash, job_result, jupyter
from ..tools.defined import DefinedTool, ToolCodeError

from .graders import grade
from .spec import TaskSpec

EVAL_ROOT = Path(__file__).resolve().parent
DEFINED_ROOT = EVAL_ROOT / ".agent-tools"  # define_tool 评测隔离目录
OUT_DIR = EVAL_ROOT / "data" / "out"  # 任务写文件的目标目录


@dataclass
class TaskResult:
    """单个任务一次的完整结果（含失败原因与指标）。"""

    task: TaskSpec
    success: bool
    detail: list[str] = field(default_factory=list)
    turns: int = 0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_errors: int = 0
    elapsed: float = 0.0
    error: str | None = None  # 任务级失败（timeout / provider error）
    output: str | None = None
    last_agent: str | None = None

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class TrackedProvider:
    """包装真实 provider：拦截 create 累计 token 用量。

    框架的 InMemoryMessages 不保存 usage（生产由 SQLite 记），评测在
    provider 层计数，不依赖消息存储。
    """

    def __init__(self, provider: OpenAIProvider):
        self._provider = provider
        self.model = provider.model
        self.usage = Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        self.calls = 0
        original = provider.create

        async def _tracked(**kwargs: Any) -> Any:
            resp = await original(**kwargs)
            self.calls += 1
            if resp.usage is not None:
                self.usage.add(Usage.model_validate(resp.usage))
            return resp

        provider.create = _tracked

    async def create(self, **kwargs: Any) -> Any:
        """转发到被包装 provider（其 create 已被打桩计数）。"""
        return await self._provider.create(**kwargs)


class RecordingExecutor(ToolExecutor):
    """记录每次工具调用（名字/参数/结果）与工具级错误数。"""

    def __init__(self, config: ToolConfig | None = None):
        super().__init__(config)
        self.calls: list[dict[str, Any]] = []
        self.tool_errors = 0

    async def execute_tool(
        self,
        name: str,
        args: dict[str, Any],
        tc_id: str,
        tools: dict[str, Tool],
        ctx: Any,
        *,
        notify: bool = True,
    ) -> tuple[str, Any]:
        result = await super().execute_tool(
            name, args, tc_id, tools, ctx, notify=notify
        )
        value = result[1] if isinstance(result, tuple) else result
        if isinstance(value, str) and value.startswith("Error"):
            self.tool_errors += 1
        self.calls.append({"name": name, "args": args, "result": value})
        return result


def build_tool_system() -> Callable[[], list[Tool]]:
    """构造评测工具集：内置工具 + 动态 define_tool（持久化隔离目录）。

    返回工具源 callable——与生产 Agent 的 tools 源同构，
    define_tool 定义的工具会在下一轮实时出现在 Agent 工具列表中。
    """

    defined = DefinedTool(root=DEFINED_ROOT)
    defined_tools: dict[str, Tool] = {}

    async def define_tool(name: str, code: str) -> str:
        """用代码定义一个可复用工具，立即生效（评测隔离目录持久化）。"""
        try:
            tool = defined.create(name, code)
        except (ToolCodeError, OSError, ValueError) as e:
            return f"定义失败: {e}"
        defined_tools[name] = tool
        return f"工具已定义并生效: {name}"

    base = [
        Tool.from_function(background),
        Tool.from_function(job_result),
        Tool.from_function(bash),
        Tool.from_function(jupyter),
        Tool.from_function(define_tool, name="define_tool"),
    ]

    def source() -> list[Tool]:
        return base + list(defined_tools.values())

    return source


def build_agent(task: TaskSpec, tools_src: Callable[[], list[Tool]]) -> Agent:
    """按任务规格构造 Agent（含 handoff 多智能体）。

    - handoff 任务：按 AgentSpec 程序化构造，先建全部 agent 再挂 handoff 边
    - 普通任务：加载 frontmatter agent（如 default），仅覆写工具 allowlist 与温度
    """
    if task.agents:
        agents: dict[str, Agent] = {}
        for spec in task.agents:
            agents[spec.name] = Agent(
                name=spec.name,
                description=spec.description or f"{spec.name} agent",
                instruction=spec.instruction,
                temperature=task.temperature,
                tools=tools_src,
                tool_filter=ToolFilter(allow=spec.tools),
            )
        for spec in task.agents:
            if spec.handoffs:
                agents[spec.name].handoffs = [
                    Handoff(target=agents[t]) for t in spec.handoffs
                ]
        return agents[task.agents[0].name]

    agent = Agent.load(task.agent, tools=tools_src)
    agent.tool_filter = ToolFilter(allow=task.tools)
    agent.temperature = task.temperature
    return agent


async def run_task(
    task: TaskSpec,
    provider: OpenAIProvider,
    tools_src: Callable[[], list[Tool]],
) -> TaskResult:
    """执行单个任务一次，返回完整结果。"""
    tracked = TrackedProvider(provider)
    executor = RecordingExecutor(
        ToolConfig(
            approval=ApprovalConfig(rules=[], default=ApprovalDecision.ALLOW),
            timeout=TimeoutConfig(default=60.0),
        )
    )
    env = SessionEnv(
        provider=tracked,
        channel=None,
        kernel=PyKernel(),
        messages=InMemoryMessages(),
        jobs={},
    )
    agent = build_agent(task, tools_src)
    runner = Runner(executor=executor)

    start = time.monotonic()
    error: str | None = None
    result = None
    try:
        result = await asyncio.wait_for(
            runner.run(
                agent,
                task.prompt,
                env=env,
                options=RunOptions(max_turns=task.max_turns),
            ),
            timeout=task.timeout,
        )
    except asyncio.TimeoutError:
        error = f"timeout after {task.timeout:.0f}s"
    except Exception as e:  # provider / 引擎级错误
        error = f"{type(e).__name__}: {e}"
    finally:
        await env.kernel.close()

    elapsed = time.monotonic() - start
    tool_names = [c["name"] for c in executor.calls]

    if error is not None:
        return TaskResult(
            task=task,
            success=False,
            detail=[f"task error: {error}"],
            llm_calls=tracked.calls,
            prompt_tokens=tracked.usage.prompt_tokens,
            completion_tokens=tracked.usage.completion_tokens,
            tool_calls=executor.calls,
            tool_errors=executor.tool_errors,
            elapsed=elapsed,
            error=error,
        )

    assert result is not None
    output = result.output
    output_text = str(output) if output is not None else None
    ok, details = grade(task.ground_truth, output_text, tool_names)
    return TaskResult(
        task=task,
        success=ok,
        detail=details,
        turns=result.completed_turns,
        llm_calls=tracked.calls,
        prompt_tokens=tracked.usage.prompt_tokens,
        completion_tokens=tracked.usage.completion_tokens,
        tool_calls=executor.calls,
        tool_errors=executor.tool_errors,
        elapsed=elapsed,
        output=output_text,
        last_agent=result.last_agent.name if result.last_agent else None,
    )
