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
    assert MCPRegistry(tmp_path / "mcp.yaml").load() == {}


def test_unusual_name_allowed(tmp_path):
    """单文件存储无文件系统暴露，特殊字符名（中文等）允许。"""
    store = MCPRegistry(tmp_path / "mcp.yaml")
    store.save("obsidian 库", MCPConfig(url="http://x"))
    assert "obsidian 库" in store.load()


def test_extension_registers_into_api():
    """MCPRegistry.extension 把已连接客户端的工具逐个注册进扩展 api。"""
    from ..infra import Tool

    async def fake_fn(args):
        return "x"

    class FakeClient:
        tools = [
            Tool(name="mcp_a_t1", description="t1", parameters={}, fn=fake_fn),
            Tool(name="mcp_a_t2", description="t2", parameters={}, fn=fake_fn),
        ]

    reg = MCPRegistry()
    reg._clients.append(FakeClient())

    collected: list[str] = []

    class FakeAPI:
        def register_tool(self, tool):
            collected.append(tool.name)

    reg.extension(FakeAPI())
    assert collected == ["mcp_a_t1", "mcp_a_t2"]
