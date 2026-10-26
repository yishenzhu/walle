import asyncio

from ..conf import MCPConfig
from ..tools.mcp import MCPManager


def test_roundtrip(tmp_path):
    store = MCPManager(tmp_path / "mcp.yaml")
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
    loaded = store.load()
    assert set(loaded) == {"obsidian", "fs"}
    assert loaded["obsidian"].url == "http://127.0.0.1:27123/mcp"
    assert loaded["fs"].command == "npx"
    assert loaded["fs"].args == [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/tmp",
    ]


def test_load_empty(tmp_path):
    assert MCPManager(tmp_path / "mcp.yaml").load() == {}


def test_unusual_name_allowed(tmp_path):
    """单文件存储无文件系统暴露，特殊字符名（中文等）允许。"""
    store = MCPManager(tmp_path / "mcp.yaml")
    store.save("obsidian 库", MCPConfig(url="http://x"))
    assert "obsidian 库" in store.load()


def test_plugin_registers_client_tools():
    """MCPManager.plugin 把客户端工具注册进 ToolService。"""
    from ..infra import Tool, ToolTable
    from ..plugin import PluginHost
    from ..spec import ToolService

    async def fake_fn(args):
        return "x"

    class FakeClient:
        tools = [
            Tool(name="mcp_a_t1", description="t1", parameters={}, fn=fake_fn),
            Tool(name="mcp_a_t2", description="t2", parameters={}, fn=fake_fn),
        ]

    manager = MCPManager()
    manager._clients.append(FakeClient())

    async def run():
        host = PluginHost()
        table = ToolTable()
        await host.root.register(ToolService, table)
        try:
            await host.launch([manager.plugin()])
            assert {t.name for t in table.all_tools()} == {"mcp_a_t1", "mcp_a_t2"}
        finally:
            await host.dispose()

    asyncio.run(run())
