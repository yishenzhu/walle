"""资源 id 约束：安全、可直接用作文件名（防目录穿越）。"""

import re

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
