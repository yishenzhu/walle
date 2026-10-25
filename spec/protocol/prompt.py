"""提示词组装服务能力面（对齐 harness 的 `ctx.systemPrompt`）。

各来源（builtin / skill / mcp…）把自己的 section 注册进来，组装方按作用域调一次
`assemble()`；循环只消费结果，不参与组装——这是本面存在的唯一理由。

**不含工具 schema**：线上 `tools` 是独立参数，不拼进 system 文本，循环直接取
ToolService.schemas()。harness 把 tool schema 也放在本面，是因为它有 `tool:<name>`
指引段要与 schema 同进同退；我们没有那种段落，故不引入这层间接。

组装的作用域即"为哪个 agent、哪一层派发深度组装"：section 文本按 `AssembleContext`
动态求值（如技能清单随 agent 白名单变化）。
"""

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
    """一次组装的作用域：为哪个 agent、哪一层派发深度组装。

    `scope` 为该 agent 已解析的作用域上下文（None = 按 agent 名取默认作用域）。
    子 agent 的作用域与顶层不同（额外 deny），故由调用方传入而不是在这里重算。
    """

    agent: str
    depth: int = 0
    scope: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptSection:
    """一段系统提示词。

    name 唯一（重名注册报错）；按 order 升序拼接，同级按注册序。text 可为静态
    字符串，或按组装作用域动态求值的提供者（返回空串即不产出该段）。
    """

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
