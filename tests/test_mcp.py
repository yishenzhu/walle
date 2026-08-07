import asyncio

from ..conf import MCPConfig
from ..tools.mcp import MCP
from ..tools.registry import ToolRegistry


class _FakeClient:
    """模拟已连接的 MCPClient（只用于重名测试）。"""

    def __init__(self, name):
        self._name = name

    @property
    def name(self):
        return self._name


def test_roundtrip(tmp_path):
    store = MCP(tmp_path)
    store.save("obsidian", MCPConfig(url="http://127.0.0.1:27123/mcp"))
    store.save(
        "fs",
        MCPConfig(command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]),
    )

    # 单文件保存，两个 server 在同一个 mcp.yaml
    assert (tmp_path / "mcp.yaml").exists()
    loaded = store.load_all()
    assert set(loaded) == {"obsidian", "fs"}
    assert loaded["obsidian"].url == "http://127.0.0.1:27123/mcp"
    assert loaded["fs"].command == "npx"
    assert loaded["fs"].args == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]


def test_load_all_empty(tmp_path):
    assert MCP(tmp_path).load_all() == {}


def test_unusual_name_allowed(tmp_path):
    """单文件存储无文件系统暴露，特殊字符名（中文等）允许。"""
    store = MCP(tmp_path)
    store.save("obsidian 库", MCPConfig(url="http://x"))
    assert "obsidian 库" in store.load_all()


class TestAddMcpServer:
    async def _registry(self):
        return ToolRegistry()

    def test_missing_url_and_command(self, monkeypatch, tmp_path):
        """无 url/command 的配置：连接失败，不落盘。"""
        monkeypatch.setattr("walle.conf.DOT_AGENT", tmp_path)

        async def main():
            reg = await self._registry()
            out = await reg.add_mcp("x", MCPConfig())
            assert "连接失败" in out
            assert not any(c.name == "x" for c in reg._mcp.clients)
            assert not (tmp_path / "mcp.yaml").exists()

        asyncio.run(main())

    def test_empty_name_rejected(self, monkeypatch, tmp_path):
        """空名无法连接成功（无有效 server），不落盘。"""
        monkeypatch.setattr("walle.conf.DOT_AGENT", tmp_path)

        async def main():
            reg = await self._registry()
            out = await reg.add_mcp("", MCPConfig(url="http://127.0.0.1:1"))
            assert "连接失败" in out
            assert not (tmp_path / "mcp.yaml").exists()

        asyncio.run(main())

    def test_connect_failure_not_persisted(self, monkeypatch, tmp_path):
        """连接失败返回错误提示，且不落盘。"""
        monkeypatch.setattr("walle.conf.DOT_AGENT", tmp_path)

        async def main():
            reg = await self._registry()
            out = await reg.add_mcp(
                "dead", MCPConfig(url="http://127.0.0.1:1")  # 未监听端口，快速连接拒绝
            )
            assert "连接失败" in out
            assert not (tmp_path / "mcp.yaml").exists()

        asyncio.run(main())

    def test_duplicate_name_rejected(self, monkeypatch, tmp_path):
        """重名 server 直接拒绝，不连接不落盘。"""
        monkeypatch.setattr("walle.conf.DOT_AGENT", tmp_path)

        async def main():
            reg = await self._registry()
            reg._mcp.clients.append(_FakeClient("obsidian"))
            out = await reg.add_mcp("obsidian", MCPConfig(url="http://127.0.0.1:1"))
            assert "已存在" in out
            assert not (tmp_path / "mcp.yaml").exists()

        asyncio.run(main())
