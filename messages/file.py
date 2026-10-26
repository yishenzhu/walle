"""文件消息存储：会话正文集中落盘，按工作目录分组。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..conf import PROJECT_ROOT
from ..infra.jsonio import atomic_write, read_json, write_json_atomic
from ..spec import Message, MessageAdapter
from .search import keyword_hits

logger = logging.getLogger(__name__)

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


def load_messages(path: Path) -> tuple[list[Message], bool]:
    """读取 JSONL；返回 (消息, 是否需要重写修复)。"""
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
    def _write(f) -> None:
        for item in items:
            f.write(item.model_dump_json())
            f.write("\n")

    atomic_write(path, _write)


def append_messages(path: Path, items: Sequence[Message]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for item in items:
            f.write(item.model_dump_json())
            f.write("\n")
        f.flush()


class FileMessages:
    """按会话目录持久化的 Messages 实现。"""

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

    async def get(self) -> list[Message]:
        await self._ensure_loaded()
        async with self._lock:
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
        await self._ensure_loaded()
        async with self._lock:
            return keyword_hits(self._items, query, limit)

    async def count(self) -> int:
        await self._ensure_loaded()
        async with self._lock:
            return len(self._items)

    async def add(self, items: Sequence[Message]):
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
