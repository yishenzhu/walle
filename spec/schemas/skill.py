"""技能声明：渐进式披露的元数据（名称/描述常驻提示词，正文按需读取）。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    """技能声明：name/description 常驻提示词，path 供 read 加载全文。"""

    name: str
    description: str
    path: str  # SKILL.md 完整路径
