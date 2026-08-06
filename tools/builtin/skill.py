import logging
import re
from pathlib import Path

import frontmatter
from pydantic import BaseModel

from ...conf import DOT_AGENT
from ..tool import Tool

logger = logging.getLogger(__name__)

_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


async def create_skill(name: str, description: str, content: str) -> str:
    """创建技能：将可复用的做法沉淀为 Skill。在用户要求时触发。"""
    if not _SKILL_NAME_RE.fullmatch(name):
        return f"创建失败: 技能名仅允许字母、数字、下划线、连字符: {name!r}"
    if not description.strip() or not content.strip():
        return "创建失败: description 与 content 不能为空"

    post = frontmatter.Post(content, name=name, description=description)
    text = frontmatter.dumps(post)

    skill_dir = Skill.ROOT_DIR / name
    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / Skill.FILENAME).write_text(text, encoding="utf-8")
    except OSError as e:
        return f"创建失败: {e}"
    logger.info(f"skill created: {skill_dir / Skill.FILENAME}")
    return f"技能已创建: {name}（下次启动生效）"


class SkillMeta(BaseModel):
    name: str
    description: str


class Skill:
    FILENAME = "SKILL.md"
    ROOT_DIR = DOT_AGENT / "skills"

    def __init__(self, dir: Path):
        self._dir = dir
        self._post = frontmatter.load(dir / self.FILENAME)
        self._meta = SkillMeta.model_validate(self._post.metadata)

        if self._dir.name != self._meta.name:
            raise ValueError(
                f"skill dir name '{self._dir.name}' not match meta name '{self._meta.name}'"
            )

    @property
    def name(self) -> str:
        return self._meta.name

    @property
    def description(self) -> str:
        return self._meta.description

    def as_tool(self) -> Tool:
        async def fn():
            return {
                "dir": str(self._dir),
                "instruction": self._post.content,
            }

        return Tool.from_function(fn, self.name, self.description)

    @classmethod
    def load(cls, root_dir: Path | None = None) -> list[Tool]:
        root = root_dir or cls.ROOT_DIR
        if not root.exists():
            return []

        tools: list[Tool] = []
        for p in sorted(root.iterdir()):
            if p.is_dir() and (p / cls.FILENAME).exists():
                try:
                    tools.append(cls(p).as_tool())
                except Exception as e:
                    logger.warning(f"skill load failed ({p.name}): {e}")

        logger.info(f"skill loaded: {len(tools)} skills")
        return tools