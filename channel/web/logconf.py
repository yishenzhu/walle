"""uvicorn 日志配置：访问日志可额外落盘到 $WALLE_HOME/logs/access.log。"""

from __future__ import annotations

import os
from typing import Any

from ...conf import LOGS

ENABLED = ("1", "true", "yes", "on")


def access_log_config() -> dict[str, Any] | None:
    """WALLE_ACCESS_LOG 未开启时返回 None，交给 uvicorn 默认配置。"""
    if os.getenv("WALLE_ACCESS_LOG", "").lower() not in ENABLED:
        return None
    LOGS.mkdir(parents=True, exist_ok=True)
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(message)s",
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "access_file": {
                "formatter": "access",
                "class": "logging.FileHandler",
                "filename": str(LOGS / "access.log"),
                "encoding": "utf-8",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {
                "handlers": ["access", "access_file"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }
