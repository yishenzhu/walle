"""审计日志：审批决策与工具执行的 JSON 行，独立于应用日志。"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

from ..conf import LOGS

logger = logging.getLogger(__name__)

AUDIT_FILE = "audit.jsonl"
DISABLED = ("0", "false", "no", "off")

_lock = threading.Lock()


def audit_enabled() -> bool:
    return os.getenv("WALLE_AUDIT", "1").lower() not in DISABLED


def audit(event: str, **fields) -> None:
    """写一条审计记录；写失败只告警，不影响业务。"""
    if not audit_enabled():
        return
    payload = {"ts": round(time.time(), 3), "event": event, **fields}
    line = json.dumps(payload, ensure_ascii=False, default=str)
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with _lock, open(LOGS / AUDIT_FILE, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        logger.warning(f"audit write failed: {exc}")
