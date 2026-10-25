"""技能服务能力面：技能目录（name → 描述/路径）。

技能 = 渐进式披露的可用清单：清单进系统提示词（由技能提供者注册成
PromptService 的一个 section），模型按需用 read 加载 SKILL.md 全文。
本面只管目录；**渲染提示词不在这里，也不在 Agent 上**——见 tools/skill.py。

按 glob 筛选与工具一致（include 白名单 / exclude 拒绝）。
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillService(Protocol):
    """会话技能目录：注册/移除/列表（含 glob 筛选）。"""

    def add(self, name: str, description: str, path: str) -> None: ...

    def remove(self, name: str) -> None: ...

    def list(
        self,
        *,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> list[Any]:
        """技能列表（name→描述/路径；include None=全部，[]=空集，exclude 优先）。"""
