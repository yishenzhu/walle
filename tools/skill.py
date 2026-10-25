"""技能（Skill）：渐进式披露的提示词资源，不是工具。

技能目录由 SkillService 提供；skill 插件扫描 .agent/skills/ 注册声明。
会话从服务取清单注入 system prompt，模型按需用 read 加载全文。
"""

import logging
from pathlib import Path

import frontmatter

from ..conf import AGENT_DIR
from ..infra import PluginAPI, Skill
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

    @classmethod
    async def legacy_plugin(cls, api: PluginAPI) -> None:
        """兼容旧插件 API：把技能声明注册进旧插件声明。"""
        for meta in cls.scan():
            api.register_skill(meta.name, meta.description, meta.path)


@plugin(name="skill", inject=(SkillService,))
async def native_skill(ctx, config) -> None:
    """技能插件：扫描 .agent/skills/，注册到会话的 SkillService。"""
    skills = ctx.get(SkillService)
    for meta in SkillLoader.scan(config.get("root")):
        skills.add(meta.name, meta.description, meta.path)
