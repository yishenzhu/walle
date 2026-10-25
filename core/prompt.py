"""系统提示词组装的默认实现（对齐 harness 的 `ctx.systemPrompt`）。

注册表 + 一次组装：section 按 (order, name) 拼接。

**分层**（照搬 ToolService 的做法）：全局注册表 + 各 agent 作用域的注册表。
作用域注册表继承全局的段，叠加自己那层；同名段本层掩盖全局。于是"某 agent
独有的提示词段"由挂进它作用域的插件注册——插件不必回读 Agent 字段，核心也不必
认识插件的配置。

工具 schema 不经本面：线上 `tools` 是独立参数，循环直接取 ToolService.schemas()。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..plugin import plugin
from ..spec import (
    AssembleContext,
    PromptAssembly,
    PromptSection,
    PromptService,
)

logger = logging.getLogger(__name__)

INSTRUCTION_SECTION = "agent:instruction"


class Prompts:
    """PromptService 能力的默认实现：段注册表。

    `base` 为继承来源（全局注册表在作用域里是 base=None；agent 作用域的注册表
    以全局为 base）。本层的注册与 base 的注册合并：**同名段本层掩盖全局**。
    """

    def __init__(self, base: Prompts | None = None) -> None:
        self._base = base
        self._sections: dict[str, PromptSection] = {}  # 本层段（重名报错）

    def section(self, section: PromptSection) -> Callable[[], None]:
        """注册 prompt 段到**本层**；本层重名报错（可掩盖 base 的同名段）。"""
        if section.name in self._sections:
            raise ValueError(f"prompt 段重名: {section.name}")
        self._sections[section.name] = section

        def _off() -> None:
            self._sections.pop(section.name, None)

        return _off

    def _all_sections(self) -> dict[str, PromptSection]:
        """本层 ⊕ base（本层掩盖同名）。"""
        merged = self._base._all_sections() if self._base is not None else {}
        merged.update(self._sections)
        return merged

    async def assemble(self, context: AssembleContext) -> PromptAssembly:
        """组装：段按 (order, name) 升序拼接（空串不产出）。"""
        sections: list[str] = []
        for section in sorted(self._all_sections().values(), key=lambda s: (s.order, s.name)):
            text = section.text(context) if callable(section.text) else section.text
            if text:
                sections.append(text)
        return PromptAssembly(sections=sections)


@plugin(name="system-prompt", provides=(PromptService,))
async def native_system_prompt(ctx, config) -> None:
    """系统提示词组装默认实现：发布**全局** PromptService 能力。

    各 agent 作用域的注册表由 Agents.ctx_for 以本表为 base 建立；instruction
    段也由它在建作用域时按定义注册（本插件不认识 Agent 定义）。
    """
    await ctx.register(PromptService, Prompts())
