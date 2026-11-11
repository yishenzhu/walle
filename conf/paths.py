"""运行期路径：只读的源码目录与可写的 WALLE_HOME 分离。"""

from __future__ import annotations

import os
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SOURCE_ROOT

HOME = Path(os.getenv("WALLE_HOME") or SOURCE_ROOT).resolve()
DATA = HOME / "data"
LOGS = HOME / "logs"
AGENT_DIR = HOME / ".agent"
CANVAS_ROOT = DATA / "canvas"
CONF_PATH = HOME / "conf.yaml"

TEMPLATES = Path(os.getenv("WALLE_TEMPLATES") or SOURCE_ROOT / "templates").resolve()
