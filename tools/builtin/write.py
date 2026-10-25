"""write 工具：整文件写入（新建或覆盖），与 read/edit 同源路径语义。"""

import codecs

from .paths import resolve_tool_path

MAX_BYTES = 256 * 1024  # 同 read：256KB 上限


async def write(path: str, content: str = "") -> str:
    """Write full content to a file, creating or overwriting it.
    Existing newline style and BOM are preserved."""
    if not path:
        return "Error: path is required"
    p = resolve_tool_path(path)
    if p.exists() and not p.is_file():
        return f"Error: not a file: {path}"
    if len(content.encode("utf-8")) > MAX_BYTES:
        return f"Error: content too large (> {MAX_BYTES // 1024}KB): {path}"

    # 覆盖已有文件：沿用其 BOM 与换行风格；新建文件按 LF 写入
    existed = p.is_file()
    bom = False
    ending = "\n"
    if existed:
        raw = p.read_bytes()
        bom = raw.startswith(codecs.BOM_UTF8)
        if b"\r\n" in (raw[len(codecs.BOM_UTF8) :] if bom else raw):
            ending = "\r\n"

    text = content.replace("\r\n", "\n").replace("\n", ending)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((codecs.BOM_UTF8 if bom else b"") + text.encode("utf-8"))
    except OSError as exc:
        return f"Error: cannot write {path}: {exc}"
    action = "updated" if existed else "created"
    return f"wrote {path}: {len(text)} chars ({action})"
