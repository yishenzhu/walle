"""技能（Skill）：渐进式披露的提示词资源，不是工具。

每个技能是 .agent/skills/<name>/SKILL.md（frontmatter name/description +
正文指令）。技能作为单个"skills"扩展声明：Skill.as_ext 扫全部技能目录，
把清单（名 + 描述 + 路径）注册进扩展——会话激活该扩展后技能清单注入
system prompt，模型按需用 read 工具加载全文。
"""

import logging
from pathlib import Path

import frontmatter
from pydantic import BaseModel

from ..conf import DOT_AGENT
from ..infra import ExtensionAPI

logger = logging.getLogger(__name__)


class SkillMeta(BaseModel):
    """技能元数据：name/description 常驻提示词，path 供 read 工具按需读全文。"""

    name: str
    description: str
    path: str  # SKILL.md 完整路径


class Skill:
    FILENAME = "SKILL.md"

    def __init__(self, dir: Path):
        self._dir = dir
        self._post = frontmatter.load(dir / self.FILENAME)
        meta = self._post.metadata
        self._name: str = meta.get("name")
        self._description: str = meta.get("description")
        if not self._name or not self._description:
            raise ValueError(f"skill missing name/description: {dir}")
        if self._dir.name != self._name:
            raise ValueError(
                f"skill dir name '{self._dir.name}' not match meta name '{self._name}'"
            )

    @property
    def meta(self) -> SkillMeta:
        return SkillMeta(
            name=self._name,
            description=self._description,
            path=str(self._dir / self.FILENAME),
        )

    @classmethod
    def scan(cls, root: Path | None = None) -> list[SkillMeta]:
        """扫描技能目录，返回全部技能元数据（单个损坏跳过并告警）。"""
        root = root or DOT_AGENT / "skills"
        if not root.exists():
            return []

        metas: list[SkillMeta] = []
        for p in sorted(root.iterdir()):
            if p.is_dir() and (p / cls.FILENAME).exists():
                try:
                    metas.append(cls(p).meta)
                except Exception as e:
                    logger.warning(f"skill load failed ({p.name}): {e}")

        logger.info(f"skill scanned: {len(metas)} skills")
        return metas

    @classmethod
    async def as_ext(cls, api: ExtensionAPI) -> None:
        """技能扩展：扫描 .agent/skills/ 把全部技能声明注册进扩展。

        会话激活该扩展后，技能清单（名/描述/路径）注入 system prompt，
        模型按需用 read 加载对应 SKILL.md 全文（渐进式披露）。
        """
        for meta in cls.scan():
            api.register_skill(meta.name, meta.description, meta.path)
