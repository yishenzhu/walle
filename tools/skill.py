"""技能（Skill）：渐进式披露的提示词资源，不是工具。"""

import logging
from pathlib import Path

import frontmatter

from ..conf import AGENT_DIR
from ..infra import Skill
from ..plugin import plugin
from ..spec import SkillService

logger = logging.getLogger(__name__)


class SkillLoader:
    FILENAME = "SKILL.md"

    def __init__(self, dir: Path):
        self._dir = dir
        meta = frontmatter.load(dir / self.FILENAME).metadata
        self._name: str = meta.get("name")
        self._description: str = meta.get("description")
        if not self._name or not self._description:
            raise ValueError(f"skill missing name/description: {dir}")
        if self._dir.name != self._name:
            raise ValueError(
                f"skill dir name '{self._dir.name}' not match meta name '{self._name}'"
            )

    @property
    def meta(self) -> Skill:
        return Skill(
            name=self._name,
            description=self._description,
            path=str(self._dir / self.FILENAME),
        )

    @classmethod
    def scan(cls, root: Path | None = None) -> list[Skill]:
        """扫描技能目录，返回全部技能声明（单个损坏跳过并告警）。"""
        root = root or AGENT_DIR / "skills"
        if not root.exists():
            return []

        metas: list[Skill] = []
        for p in sorted(root.iterdir()):
            if p.is_dir() and (p / cls.FILENAME).exists():
                try:
                    metas.append(cls(p).meta)
                except Exception as e:
                    logger.warning(f"skill load failed ({p.name}): {e}")

        logger.info(f"skill scanned: {len(metas)} skills")
        return metas


@plugin(name="skill", inject=(SkillService,))
async def native_skill(ctx, config) -> None:
    """技能插件：扫描 .agent/skills/，注册到会话的 SkillService。"""
    skills = ctx.get(SkillService)
    for meta in SkillLoader.scan(config.get("root")):
        skills.add(meta.name, meta.description, meta.path)
