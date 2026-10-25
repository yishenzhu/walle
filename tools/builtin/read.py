"""read 工具：读取文本文件（技能按需加载全文的入口，也可读会话文件）。"""

from .paths import resolve_tool_path

MAX_BYTES = 256 * 1024  # 256KB 上限，防误读大文件/二进制


async def read(path: str = "") -> str:
    """Read a text file and return its content.
    `path` is absolute or session-relative."""
    if not path:
        return "Error: path is required"
    p = resolve_tool_path(path)
    if not p.exists() or not p.is_file():
        return f"Error: file not found: {path}"
    if p.stat().st_size > MAX_BYTES:
        return f"Error: file too large (> {MAX_BYTES // 1024}KB): {path}"
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Error: not a text file: {path}"
