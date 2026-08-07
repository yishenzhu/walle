import logging
from pathlib import Path

import frontmatter
from pydantic import BaseModel

from ...conf import DOT_AGENT
from ..tool import Tool

logger = logging.getLogger(__name__)


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