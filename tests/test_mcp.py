import asyncio

from ..conf import MCPConfig
from ..tools.mcp import MCPRegistry


def test_roundtrip(tmp_path):
    store = MCPRegistry(tmp_path / "mcp.yaml")
    store.save("obsidian", MCPConfig(url="http://127.0.0.1:27123/mcp"))
    store.save(
        "fs",
        MCPConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        ),
    )

    # 单文件保存，两个 server 在同一个 mcp.yaml
    assert (tmp_path / "mcp.yaml").exists()
    loaded = store.load_all()
    assert set(loaded) == {"obsidian", "fs"}
    assert loaded["obsidian"].url == "http://127.0.0.1:27123/mcp"
    assert loaded["fs"].command == "npx"
    assert loaded["fs"].args == [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/tmp",
    ]


def test_load_all_empty(tmp_path):
    assert MCPRegistry(tmp_path / "mcp.yaml").load_all() == {}


def test_unusual_name_allowed(tmp_path):
    """单文件存储无文件系统暴露，特殊字符名（中文等）允许。"""
    store = MCPRegistry(tmp_path / "mcp.yaml")
    store.save("obsidian 库", MCPConfig(url="http://x"))
    assert "obsidian 库" in store.load_all()
