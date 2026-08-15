"""τ-bench 适配层：把 τ-bench 环境折叠成 Walle 工具集，由 Walle Runner 驱动。

设计原则：
- 借 τ-bench 的数据集 + 状态机环境（env.step），不借它的 agent 协议与 LLM 调用
- 环境（工具执行 + user simulator）全部包装为 Walle 工具：模型调工具 → env.step
- 评分：env.step 在 done 时自动计算 reward（数据库状态 hash + 输出匹配），直接取用
- user simulator 用同步 OpenAI client 走同一网关（litellm 默认端点不可达）
"""

import asyncio
import logging
from typing import Any, Callable

from openai import OpenAI

from tau_bench.envs.base import Env
from tau_bench.types import RESPOND_ACTION_NAME, Action

from ...tools import Tool

from ..spec import GroundTruth, TaskSpec

logger = logging.getLogger(__name__)

DEFAULT_MAX_TURNS = 40  # τ-bench 官方 max_steps 同量级


class WalleUserSimulationEnv:
    """user simulator：同步 OpenAI client 走同一网关，官方 prompt 模板原样。

    实现 BaseUserSimulationEnv 协议（reset/step/get_total_cost），构造 env 后
    替换 env.user 即可（Env 只依赖这三个方法）。token 自行累计。
    """

    def __init__(self, api_key: str, base_url: str, model: str):
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.messages: list[dict[str, Any]] = []
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def _generate(self, messages: list[dict[str, Any]]) -> str:
        resp = self._client.chat.completions.create(
            model=self.model, messages=messages, temperature=0
        )
        msg = resp.choices[0].message
        self.messages.append(msg.model_dump())
        if resp.usage is not None:
            self.prompt_tokens += resp.usage.prompt_tokens or 0
            self.completion_tokens += resp.usage.completion_tokens or 0
        return msg.content or ""

    def build_system_prompt(self, instruction: str | None) -> str:
        instruction_display = (
            ("\n\nInstruction: " + instruction + "\n") if instruction is not None else ""
        )
        return f"""You are a user interacting with an agent.{instruction_display}
Rules:
- Just generate one line at a time to simulate the user's message.
- Do not give away all the instruction at once. Only provide the information that is necessary for the current step.
- Do not hallucinate information that is not provided in the instruction. For example, if the agent asks for the order id but it is not mentioned in the instruction, do not make up an order id, just say you do not remember or have it.
- If the instruction goal is satisified, generate '###STOP###' as a standalone message without anything else to end the conversation.
- Do not repeat the exact instruction in the conversation. Instead, use your own words to convey the same information.
- Try to make the conversation as natural as possible, and stick to the personalities in the instruction."""

    def reset(self, instruction: str | None = None) -> str:
        self.messages = [
            {"role": "system", "content": self.build_system_prompt(instruction)},
            {"role": "user", "content": "Hi! How can I help you today?"},
        ]
        return self._generate(self.messages)

    def step(self, content: str) -> str:
        self.messages.append({"role": "user", "content": content})
        return self._generate(self.messages)

    def get_total_cost(self) -> float:
        return 0.0  # 成本按 token 在报告侧计算（--price-*）


def build_tau_tools(env: Env) -> tuple[list[Tool], dict[str, Any]]:
    """把 τ-bench 环境折叠成 Walle 工具集。

    每个领域工具 fn 内部调 env.step（同步），返回 observation（工具输出或用户回复）；
    respond 工具回复用户。所有 step 用同一把锁串行（模型可能并行发起 tool_calls，
    而 env 共享数据库状态，必须保持顺序）。
    返回 (工具列表, 共享状态)，state["reward"] 在对话 done 时写入。
    """
    state: dict[str, Any] = {"reward": None, "stopped": False}
    lock = asyncio.Lock()

    def make_step_fn(name: str) -> Callable[[dict[str, Any]], Any]:
        async def fn(args: dict[str, Any]) -> str:
            async with lock:
                resp = env.step(Action(name=name, kwargs=args))
            if resp.done:
                state["reward"] = resp.reward
            return resp.observation

        return fn

    tools: list[Tool] = []
    for ti in env.tools_info:
        f = ti["function"]
        tools.append(
            Tool(
                name=f["name"],
                description=f["description"],
                parameters=f["parameters"],
                fn=make_step_fn(f["name"]),
            )
        )

    async def respond(args: dict[str, Any]) -> str:
        async with lock:
            if state["stopped"]:
                return "对话已结束。"
            resp = env.step(Action(name=RESPOND_ACTION_NAME, kwargs=args))
            if resp.done:
                state["reward"] = resp.reward
                state["stopped"] = True
            return resp.observation

    tools.append(
        Tool(
            name=RESPOND_ACTION_NAME,
            description=(
                "向用户发送一条消息（回答用户的问题，或在需要时向用户提问）。"
                "完成任务后回复用户即可结束对话。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "要发送给用户的消息内容"}
                },
                "required": ["content"],
            },
            fn=respond,
        )
    )
    return tools, state


def make_task_spec(idx: int, first_observation: str, env_name: str) -> TaskSpec:
    """把 τ-bench 用例包装成 TaskSpec（复用现有 metrics/report 管线）。

    success 不经过 graders（由 τ-bench reward 决定），ground_truth 仅为满足 schema。
    """
    return TaskSpec(
        name=f"tau_{env_name}_{idx}",
        domain=f"tau_{env_name}",
        prompt=first_observation,
        max_turns=DEFAULT_MAX_TURNS,
        timeout=900.0,
        temperature=0.0,
        ground_truth=GroundTruth(type="contains", value=""),
    )
