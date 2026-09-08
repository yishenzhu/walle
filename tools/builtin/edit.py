"""edit 工具：与 read 对称的局部查找替换（old 须唯一匹配）。

匹配按严格度降级：精确子串 → 忽略行尾空白 → 忽略行首尾空白
（末级命中后按原缩进重排新内容）。换行风格（LF/CRLF）与 BOM 按原文件保真。
"""

import codecs
from pathlib import Path

MAX_BYTES = 256 * 1024  # 同 read：256KB 上限

# 降级匹配的空白比较函数，按严格度递进
COMPARE = (str.rstrip, str.strip)


def match_exact(text: str, old: str) -> list[tuple[int, int]]:
    """精确子串匹配的全部区间。"""
    spans: list[tuple[int, int]] = []
    start = text.find(old)
    while start != -1:
        spans.append((start, start + len(old)))
        start = text.find(old, start + len(old))
    return spans


def match_lines(text: str, old: str, cmp) -> list[tuple[int, int]]:
    """按行窗口匹配（cmp 决定空白严格度）的全部区间。

    text/old 均为 LF 归一化文本。
    """
    lines = text.split("\n")
    pat = old.split("\n")
    trailing = pat[-1] == ""  # old 以换行结尾：区间连末尾换行一起吃掉
    if trailing:
        pat.pop()
    if not pat:
        return []
    starts: list[int] = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line) + 1
    spans: list[tuple[int, int]] = []
    for i in range(len(lines) - len(pat) + 1):
        if all(cmp(lines[i + j]) == cmp(pat[j]) for j in range(len(pat))):
            last = i + len(pat) - 1
            end = starts[last] + len(lines[last]) + (1 if trailing else 0)
            spans.append((starts[i], end))
    return spans


async def edit(path: str, old_text: str, new_text: str = "") -> str:
    """Replace one text fragment in a file.

    Read the file first via `read`; old_text must match exactly once.
    Whitespace/indentation differences are tolerated as a fallback.
    Args:
        path: Absolute or project-relative path (same as `read`).
        old_text: Text to replace.
        new_text: Replacement (empty deletes).
    """
    if not path:
        return "Error: path is required"
    if old_text == "":
        return "Error: old_text must not be empty (read the file first for exact content)"
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return f"Error: file not found: {path}"
    if p.stat().st_size > MAX_BYTES:
        return f"Error: file too large (> {MAX_BYTES // 1024}KB): {path}"

    raw = p.read_bytes()
    bom = raw.startswith(codecs.BOM_UTF8)
    try:
        text = (raw[len(codecs.BOM_UTF8) :] if bom else raw).decode("utf-8")
    except UnicodeDecodeError:
        return f"Error: not a text file: {path}"

    # 内部一律 LF 归一化，写回时按原文件风格还原（BOM 同理）
    ending = "\r\n" if "\r\n" in text else "\n"
    text = text.replace("\r\n", "\n")
    old = old_text.replace("\r\n", "\n")
    new = new_text.replace("\r\n", "\n")

    spans = match_exact(text, old)
    cmp = None  # 命中的降级策略（None = 精确）
    if not spans:
        for cmp in COMPARE:
            spans = match_lines(text, old, cmp)
            if spans:
                break
    if len(spans) > 1:
        return (
            f"Error: old_text matches {len(spans)} times in {path}. "
            f"Include more surrounding context to make it unique."
        )
    if not spans:
        return (
            f"Error: old_text not found in {path}. "
            f"Read the file first and copy the exact text to replace."
        )

    start, end = spans[0]
    note = ""
    if cmp is str.strip:
        # 行首空白被忽略过：新内容按命中处的缩进重排，避免写坏格式
        line = text[start:].split("\n", 1)[0]
        indent = line[: len(line) - len(line.lstrip(" \t"))]
        if indent:
            head = new.split("\n")[0]
            base = head[: len(head) - len(head.lstrip(" \t"))]
            lines = []
            for line in new.split("\n"):
                if not line.strip():
                    lines.append("")
                elif line.startswith(base):
                    lines.append(indent + line[len(base) :])
                else:
                    lines.append(indent + line.lstrip(" \t"))
            new = "\n".join(lines)
        note = " (ignored indentation)"
    elif cmp is str.rstrip:
        note = " (ignored trailing whitespace)"

    new_text = text[:start] + new + text[end:]
    try:
        p.write_bytes(
            (codecs.BOM_UTF8 if bom else b"")
            + new_text.replace("\n", ending).encode("utf-8")
        )
    except OSError as exc:
        return f"Error: cannot write {path}: {exc}"
    return f"edited {path}: replaced {len(old_text)} chars -> {len(new_text)} chars{note}"
