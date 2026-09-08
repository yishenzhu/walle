"""edit 工具：与 read 对称的局部查找替换（old 须唯一匹配）。"""

from pathlib import Path

MAX_BYTES = 256 * 1024  # 同 read：256KB 上限


async def edit(path: str, old_string: str, new_string: str = "") -> str:
    """Replace one exact text fragment in a file.

    Read the file first via `read`; old_string must match exactly once.
    Args:
        path: Absolute or project-relative path (same as `read`).
        old_string: Exact text to replace.
        new_string: Replacement (empty deletes).
    """
    if not path:
        return "Error: path is required"
    if old_string == "":
        return "Error: old_string must not be empty (read the file first for exact content)"
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return f"Error: file not found: {path}"
    if p.stat().st_size > MAX_BYTES:
        return f"Error: file too large (> {MAX_BYTES // 1024}KB): {path}"
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Error: not a text file: {path}"

    count = text.count(old_string)
    if count == 0:
        return (
            f"Error: old_string not found in {path}. "
            f"Read the file first and copy the exact text to replace."
        )
    if count > 1:
        return (
            f"Error: old_string matches {count} times in {path}. "
            f"Include more surrounding context to make it unique."
        )

    new_text = text.replace(old_string, new_string, 1)
    try:
        p.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return f"Error: cannot write {path}: {exc}"
    return f"edited {path}: replaced {len(old_string)} chars -> {len(new_string)} chars"
