"""提示词资源加载：从 .agent/prompts/ 目录读纯正文 md。

与 .agent/agents（frontmatter + 正文）不同，这里的 md 是纯系统提示词，
无结构化元数据。放 .agent/prompts/ 使提示词与代码分离，可独立编辑。
"""

from pathlib import Path

from ..conf import DOT_AGENT

PROMPTS_DIR = DOT_AGENT / "prompts"


def load_prompt(name: str) -> str:
    """读 .agent/prompts/<name>.md 的纯正文（strip 首尾空白）。

    Raises:
        FileNotFoundError: 提示词文件不存在。
    """
    path = Path(PROMPTS_DIR) / f"{name}.md"
    return path.read_text(encoding="utf-8").strip()
