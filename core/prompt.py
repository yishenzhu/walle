"""系统提示词组装的默认实现（对齐 harness 的 `ctx.systemPrompt`）。"""

from __future__ import annotations

from collections.abc import Callable

from ..spec import (
    AssembleContext,
    PromptAssembly,
    PromptSection,
)

INSTRUCTION_SECTION = "agent:instruction"


class Prompts:
    """PromptService 能力的默认实现：段注册表。"""

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
