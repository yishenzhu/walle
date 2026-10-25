"""τ-bench 适配层：把 τ-bench 环境折叠成 Walle 工具集，由 Walle Runner 驱动。"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from openai import OpenAI
from tau_bench.envs.base import Env
from tau_bench.types import RESPOND_ACTION_NAME, Action

from ...infra import Tool
from ..spec import GroundTruth, TaskSpec

logger = logging.getLogger(__name__)

DEFAULT_MAX_TURNS = 40  # τ-bench 官方 max_steps 同量级


class WalleUserSimulationEnv:
    """user simulator：同步 OpenAI client 走同一网关，官方 prompt 模板原样。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.messages: list[dict[str, Any]] = []
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def _generate(self, messages: list[dict[str, Any]]) -> str:
        response = self._client.chat.completions.create(
            model=self.model, messages=messages, temperature=0
        )
        message = response.choices[0].message
        self.messages.append(message.model_dump())
        if response.usage is not None:
            self.prompt_tokens += response.usage.prompt_tokens or 0
            self.completion_tokens += response.usage.completion_tokens or 0
        return message.content or ""

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
    """把 τ-bench 环境折叠成 Walle 工具集。"""
    state: dict[str, Any] = {"reward": None, "stopped": False}
    lock = asyncio.Lock()

    def make_step_fn(name: str) -> Callable[[dict[str, Any]], Any]:
        async def fn(args: dict[str, Any]) -> str:
            async with lock:
                step_result = env.step(Action(name=name, kwargs=args))
            if step_result.done:
                state["reward"] = step_result.reward
            return step_result.observation

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
            step_result = env.step(Action(name=RESPOND_ACTION_NAME, kwargs=args))
            if step_result.done:
                state["reward"] = step_result.reward
                state["stopped"] = True
            return step_result.observation

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


def build_task_spec(idx: int, first_observation: str, env_slug: str) -> TaskSpec:
    """把 τ-bench 用例包装成 TaskSpec（复用现有 metrics/report 管线）。"""
    return TaskSpec(
        name=f"tau_{env_slug}_{idx}",
        domain=f"tau_{env_slug}",
        prompt=first_observation,
        max_turns=DEFAULT_MAX_TURNS,
        timeout=900.0,
        temperature=0.0,
        ground_truth=GroundTruth(type="contains", value=""),
    )
