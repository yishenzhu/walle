"""Tool 与 ToolRegistry 测试。"""

import pytest

from ..tools import Tool, ToolRegistry
from ..tools.tool import ToolContext, tool_context


class TestTool:
    async def test_run_calls_fn(self):
        async def echo(args):
            return f"echo: {args['msg']}"

        tool = Tool(name="echo", description="echo tool", parameters={}, fn=echo)
        result = await tool.run({"msg": "hello"})
        assert result == "echo: hello"

    def test_formatted_schema(self):
        async def dummy(args):
            return ""

        tool = Tool(
            name="bash",
            description="run bash",
            parameters={"type": "object", "properties": {"cmd": {"type": "string"}}},
            fn=dummy,
        )
        schema = tool.formatted_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "bash"
        assert schema["function"]["description"] == "run bash"
        assert schema["function"]["strict"] is True
        assert schema["function"]["parameters"]["additionalProperties"] is False

    def test_formatted_schema_non_strict(self):
        async def dummy(args):
            return ""

        tool = Tool(name="t", description="d", parameters={}, fn=dummy)
        schema = tool.formatted_schema(strict=False)
        assert schema["function"]["strict"] is False
        assert "additionalProperties" not in schema["function"]["parameters"]

    async def test_from_function(self):
        async def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        tool = Tool.from_function(add)
        assert tool.name == "add"
        assert "a" in tool.parameters["properties"]
        assert "b" in tool.parameters["properties"]
        result = await tool.run({"a": 1, "b": 2})
        assert result == 3


class TestToolRegistry:
    """ToolRegistry = 纯工具容器：注册 / 查重 / 查询，不认识具体工具。"""

    @staticmethod
    def _tool(name: str) -> Tool:
        async def fn(args):
            return name

        return Tool(name=name, description=name, parameters={"type": "object"}, fn=fn)

    @pytest.fixture
    async def registry(self, tmp_path, monkeypatch):
        """已连接 MCP 的空 ToolRegistry（无任何本地工具）。"""
        from ..conf import Config, LogConfig
        from ..tools import mcp as mcp_mod

        monkeypatch.setattr("walle.conf.DOT_AGENT", tmp_path)
        monkeypatch.setattr(mcp_mod, "DOT_AGENT", tmp_path)  # MCP 模块内值绑定
        conf = Config(
            log=LogConfig(level="INFO", path="x.log", backup_count=1),
        )
        reg = ToolRegistry()
        await reg.initialize(conf)
        yield reg
        await reg.close()

    async def test_empty_registry_has_no_local_tools(self, registry):
        """纯容器初始为空（内置工具由引导扩展注册，不属于 registry）。"""
        assert registry.all_tools() == []

    async def test_add_tool_duplicate_raises(self):
        registry = ToolRegistry()
        registry.add_tool(self._tool("x"))
        with pytest.raises(ValueError, match="Duplicate tool name"):
            registry.add_tool(self._tool("x"))

    async def test_add_tool_batch_atomic(self):
        """整批注册：其中重名则整批抛错，不部分生效。"""
        registry = ToolRegistry()
        registry.add_tool(self._tool("a"))
        with pytest.raises(ValueError):
            registry.add_tool(self._tool("a"), self._tool("b"))
        assert {t.name for t in registry.all_tools()} == {"a"}  # b 未部分注册

    async def test_all_tools_returns_added(self):
        registry = ToolRegistry()

        async def custom_tool(x: str) -> str:
            """custom"""
            return x

        registry.add_tool(Tool.from_function(custom_tool))
        names = {t.name for t in registry.all_tools()}
        assert "custom_tool" in names

    async def test_mcp_empty(self, registry):
        """空 mcp.yaml：无 MCP 远端工具。"""
        assert registry.all_tools() == []
