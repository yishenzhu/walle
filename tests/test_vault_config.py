import asyncio
import tempfile
from pathlib import Path

from ..conf import Config


def test_vault_config_loads():
    """真实 conf.yaml 的 vault 路径可访问且含 md 笔记。"""
    conf = Config.load()
    assert conf.vault.enabled, "vault.enabled 应为 true"
    p = Path(conf.vault.path)
    assert p.exists(), f"路径不存在: {p}"
    md_files = list(p.rglob("*.md"))
    assert md_files, "vault 下没有 md 文件"
    print(f"vault path: {p}, md 文件数: {len(md_files)}")
