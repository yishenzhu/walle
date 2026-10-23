"""文件消息存储：会话正文集中落盘，按工作目录分组。

目录布局（全局数据目录，不写进用户仓库）：

    data/sessions/<workspace_id>/
        workspace.json     # 工作目录身份（canonical path / 名称 / 时间）
        <session>/
            meta.json      # 会话身份、工作目录与时间戳，供列表/恢复使用
            messages.jsonl # 追加写原文，一行一条消息
            projection.json# new_window 切点（可选）

JSONL 只追加，崩溃最多留下一条不完整行；读取时跳过损坏行并原子重写修复。
同一会话只允许单写者（与 Session 的单写者模型一致）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..spec import Message, MessageAdapter, Usage

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSIONS_PATH = "data/sessions"
WORKSPACE_FILE = "workspace.json"
META_FILE = "meta.json"
MESSAGES_FILE = "messages.jsonl"
PROJECTION_FILE = "projection.json"
SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def canonical_cwd(cwd: str | Path | None) -> Path:
    """会话工作目录的规范形式；缺省退回服务端进程目录。"""
    base = Path(cwd).expanduser() if cwd else Path.cwd()
    return base.resolve()


def workspace_id(cwd: str | Path | None) -> str:
    """工作目录身份：canonical path 的稳定摘要。"""
    digest = hashlib.sha1(str(canonical_cwd(cwd)).encode("utf-8")).hexdigest()
    return digest[:12]


def sessions_root(path: str | Path | None = None) -> Path:
    """集中存储根；相对路径按项目根解析。"""
    root = Path(path) if path else Path(DEFAULT_SESSIONS_PATH)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    root.mkdir(parents=True, exist_ok=True)
    return root


def workspace_dir(cwd: str | Path | None, root: str | Path | None = None) -> Path:
    """取得（并登记）工作目录的会话目录。"""
    base = sessions_root(root)
    target = canonical_cwd(cwd)
    identifier = workspace_id(target)
    directory = base / identifier
    directory.mkdir(parents=True, exist_ok=True)

    now = time.time()
    path = directory / WORKSPACE_FILE
    info = read_json(path) or {}
    info["id"] = identifier
    info["path"] = str(target)
    info["name"] = target.name or str(target)
    info.setdefault("created_at", now)
    info["updated_at"] = now
    write_json_atomic(path, info)
    return directory


def session_metas(directory: Path) -> list[dict[str, Any]]:
    """读取一个 workspace 目录下所有会话的 meta（跳过损坏/非会话目录）。"""
    result: list[dict[str, Any]] = []
    if not directory.is_dir():
        return result
    for entry in directory.iterdir():
        if not entry.is_dir():
            continue
        meta = read_json(entry / META_FILE)
        if meta is None:
            continue
        meta.setdefault("session_id", entry.name)
        result.append(meta)
    return result


def list_workspaces(root: str | Path | None = None) -> list[dict[str, Any]]:
    """列出已登记的工作目录，按最近会话/登记时间倒序。"""
    base = sessions_root(root)
    result: list[dict[str, Any]] = []
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        info = read_json(entry / WORKSPACE_FILE)
        if info is None:
            continue
        metas = session_metas(entry)
        latest = max(
            (meta.get("updated_at") or 0 for meta in metas),
            default=0,
        )
        info["id"] = info.get("id") or entry.name
        info["session_count"] = len(metas)
        info["updated_at"] = max(info.get("updated_at") or 0, latest)
        result.append(info)
    result.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)
    return result


def list_sessions(cwd: str | Path, root: str | Path | None = None) -> list[dict[str, Any]]:
    """列出某个工作目录下的会话，按最近更新倒序。"""
    target = canonical_cwd(cwd)
    if not target.is_dir():
        return []
    directory = sessions_root(root) / workspace_id(target)
    result = session_metas(directory)
    for meta in result:
        meta.setdefault("cwd", str(target))
        meta.setdefault("title", meta.get("session_id"))
    result.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)
    return result


def session_dir_name(session_id: str) -> str:
    """把任意会话 id 映射成安全、稳定的目录名。"""
    if SAFE_SESSION_ID.fullmatch(session_id):
        return session_id
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", session_id).strip("-")[:40] or "session"
    digest = hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:8]
    return f"{safe}-{digest}"


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """临时文件 + replace，避免读到写了一半的元数据。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def load_messages(path: Path) -> tuple[list[Message], bool]:
    """读取 JSONL；返回 (消息, 是否需要重写修复)。

    损坏行（含崩溃留下的半行）跳过；文件末尾无换行时也重写，保证下一次
    追加不会把新消息拼到旧行尾。
    """
    if not path.exists():
        return [], False
    raw = path.read_text(encoding="utf-8", errors="replace")
    if not raw:
        return [], False
    needs_rewrite = not raw.endswith("\n")
    items: list[Message] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            items.append(MessageAdapter.validate_json(line))
        except Exception:
            needs_rewrite = True
            logger.warning("skipping malformed message line in %s", path)
    return items, needs_rewrite


def write_messages_atomic(path: Path, items: Sequence[Message]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            for item in items:
                f.write(item.model_dump_json())
                f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def append_messages(path: Path, items: Sequence[Message]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for item in items:
            f.write(item.model_dump_json())
            f.write("\n")
        f.flush()


class FileMessages:
    """按会话目录持久化的 Messages 实现。

    首次访问时把 JSONL 读入内存（会话历史规模有限），之后的追加同时写文件
    与内存；pop/clear 用原子重写。读取操作返回副本，调用方修改不污染内部。
    """

    def __init__(
        self,
        session_dir: str | Path,
        session_id: str = "default",
        cwd: str | Path | None = None,
    ):
        self._dir = Path(session_dir)
        self._session_id = session_id
        self._cwd = str(canonical_cwd(cwd)) if cwd is not None else None
        self._messages_path = self._dir / MESSAGES_FILE
        self._meta_path = self._dir / META_FILE
        self._items: list[Message] = []
        self._meta: dict[str, Any] = {}
        self._loaded = False
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        """本会话的落盘目录。"""
        return self._dir

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            await asyncio.to_thread(self._load_sync)
            self._loaded = True

    def _load_sync(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        meta = read_json(self._meta_path) or {}
        meta["session_id"] = self._session_id
        meta["workspace_id"] = self._dir.parent.name
        if self._cwd is not None:
            meta["cwd"] = self._cwd
        meta.setdefault("created_at", now)
        meta.setdefault("updated_at", meta["created_at"])
        write_json_atomic(self._meta_path, meta)
        self._meta = meta

        items, needs_rewrite = load_messages(self._messages_path)
        if needs_rewrite:
            write_messages_atomic(self._messages_path, items)
        self._items = items

    def _touch_meta_sync(self) -> None:
        self._meta["updated_at"] = time.time()
        write_json_atomic(self._meta_path, self._meta)

    def _add_sync(self, items: Sequence[Message]) -> None:
        if not self._meta.get("title"):
            for item in items:
                if item.role == "user" and (item.content or "").strip():
                    self._meta["title"] = (item.content or "").strip().splitlines()[0][:80]
                    break
        append_messages(self._messages_path, items)
        self._touch_meta_sync()

    def _write_all_sync(self, items: Sequence[Message]) -> None:
        write_messages_atomic(self._messages_path, items)
        self._touch_meta_sync()

    async def get(self, limit: int | None = None) -> list[Message]:
        await self._ensure_loaded()
        async with self._lock:
            if limit is not None:
                return list(self._items[:limit])
            return list(self._items)

    async def query(self, offset: int = 0, limit: int = 20) -> list[Message]:
        """窗口查询：按追加顺序取 [offset, offset+limit) 的原文。"""
        if offset < 0 or limit < 0:
            raise ValueError("offset/limit must be >= 0")
        await self._ensure_loaded()
        async with self._lock:
            return list(self._items[offset : offset + limit])

    async def search(self, query: str = "", limit: int = 20) -> list[tuple[int, Message]]:
        """关键词检索：词间 AND 子串匹配（大小写不敏感），倒序返回。"""
        words = [w for w in query.lower().split() if w]
        await self._ensure_loaded()
        async with self._lock:
            matched = [
                (i, m)
                for i, m in enumerate(self._items)
                if all(w in (m.content or "").lower() for w in words)
            ]
            matched.reverse()
            return matched[:limit]

    async def count(self) -> int:
        await self._ensure_loaded()
        async with self._lock:
            return len(self._items)

    async def add(self, items: Sequence[Message], usage: Usage | None = None):
        if not items:
            return
        batch = list(items)
        await self._ensure_loaded()
        async with self._lock:
            await asyncio.to_thread(self._add_sync, batch)
            self._items.extend(batch)

    async def pop(self) -> Message | None:
        await self._ensure_loaded()
        async with self._lock:
            if not self._items:
                return None
            first = self._items[0]
            await asyncio.to_thread(self._write_all_sync, self._items[1:])
            self._items.pop(0)
            return first

    async def clear(self):
        await self._ensure_loaded()
        async with self._lock:
            await asyncio.to_thread(self._write_all_sync, [])
            self._items.clear()

    async def close(self):
        pass


class FileProjectionStore:
    """切点持久化：会话目录下 projection.json。"""

    def __init__(self, path: str | Path):
        self._path = Path(path)

    async def load(self) -> int | None:
        def read_cut() -> int | None:
            data = read_json(self._path)
            cut = data.get("cut") if data else None
            if isinstance(cut, bool) or not isinstance(cut, int) or cut < 0:
                return None
            return cut

        return await asyncio.to_thread(read_cut)

    async def save(self, cut: int) -> None:
        if cut < 0:
            raise ValueError("cut must be >= 0")
        await asyncio.to_thread(write_json_atomic, self._path, {"cut": cut})

    async def clear(self) -> None:
        def remove() -> None:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass

        await asyncio.to_thread(remove)
