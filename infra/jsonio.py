"""JSON / 文本文件读写：容错读 + 原子写（临时文件 + fsync + replace）。"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO


def read_json(path: Path) -> dict[str, Any] | None:
    """读 JSON 对象；文件缺失、损坏或顶层不是对象时返回 None。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def atomic_write(path: Path, write: Callable[[TextIO], None]) -> None:
    """写入临时文件后原子替换，避免读到写了一半的文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            write(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """原子写 JSON 对象（缩进 2、保留非 ASCII、结尾换行）。"""

    def _write(f: TextIO) -> None:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    atomic_write(path, _write)


def write_text_atomic(path: Path, text: str) -> None:
    """原子写文本。"""
    atomic_write(path, lambda f: f.write(text))
