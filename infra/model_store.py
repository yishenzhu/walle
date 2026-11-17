"""服务端模型配置：唯一一份 key，落盘 WALLE_HOME/data/model.json。"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..conf import DATA
from ..spec import ModelConfig
from .jsonio import read_json, write_json_atomic

logger = logging.getLogger(__name__)

MODEL_FILE = "model.json"


def model_path() -> Path:
    """配置文件路径（容器里在持久卷上）。"""
    return DATA / MODEL_FILE


def load_model() -> ModelConfig | None:
    """读服务端配置；缺失或损坏时返回 None（不让坏文件挡住启动）。"""
    data = read_json(model_path())
    if not data:
        return None
    try:
        return ModelConfig.model_validate(data)
    except Exception as exc:
        logger.warning(f"ignoring invalid model config: {exc}")
        return None


def save_model(cfg: ModelConfig) -> None:
    """原子写入并收紧权限：key 只留给进程自己读。"""
    path = model_path()
    write_json_atomic(path, cfg.model_dump())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def clear_model() -> None:
    try:
        model_path().unlink()
    except FileNotFoundError:
        pass


def masked_model() -> dict:
    """给前端看的配置：不含 key 明文，只留后四位。"""
    cfg = load_model()
    if cfg is None:
        return {"configured": False}
    return {
        "configured": True,
        "base_url": cfg.base_url,
        "model": cfg.model,
        "key_tail": cfg.api_key[-4:] if len(cfg.api_key) > 8 else "",
        "headers": sorted((cfg.headers or {}).keys()),
    }
