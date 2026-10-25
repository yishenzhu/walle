"""提示词组装服务能力面（对齐 harness 的 `ctx.systemPrompt`）。"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Protocol, runtime_checkable


class PromptOrder(IntEnum):
    """仓库自有的段位分配（贡献者取这里的值，不各自编数字）。"""

    DEPLOYMENT = 10  # 部署级 persona 等
    AGENT_INSTRUCTION = 100  # 当前 agent 的 instruction
    SKILLS = 200  # 可用技能清单
    RUNTIME = 900  # 运行时动态上下文（靠后）


@dataclass(frozen=True)
class AssembleContext:
    """一次组装的作用域：为哪个 agent、哪一层派发深度组装。"""

    agent: str
    depth: int = 0
    scope: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptSection:
    """一段系统提示词。"""

    name: str
    order: int
    text: str | Callable[[AssembleContext], str]


@dataclass
class PromptAssembly:
    """一次组装的结果：有序 prompt 段文本。"""

    sections: list[str] = field(default_factory=list)


@runtime_checkable
class PromptService(Protocol):
    """提示词组装：注册贡献 + 一次组装。"""

    def section(self, section: PromptSection) -> Callable[[], None]:
        """注册 prompt 段（返回摘除函数）；重名报错。"""

    async def assemble(self, context: AssembleContext) -> PromptAssembly:
        """组装：有序段文本。"""
