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
    def test_builtin_tools_loaded(self):
        registry = ToolRegistry()
        names = {t.name for t in registry.builtin_tools()}
        assert "bash" in names
        assert "ask_user" in names

    def test_add_builtin_duplicate_raises(self):
        registry = ToolRegistry()

        async def bash(cmd: str = "") -> str:
            """bash"""
            return ""

        with pytest.raises(ValueError, match="Duplicate tool name"):
            registry.add_builtin(bash)

    def test_add_builtin_new(self):
        registry = ToolRegistry()

        async def custom_tool(x: str) -> str:
            """custom"""
            return x

        registry.add_builtin(custom_tool)
        names = {t.name for t in registry.builtin_tools()}
        assert "custom_tool" in names

    def test_builtin_tools_include(self):
        registry = ToolRegistry()
        tools = registry.builtin_tools(include={"bash"})
        names = {t.name for t in tools}
        assert names == {"bash"}

    def test_builtin_tools_exclude(self):
        registry = ToolRegistry()
        all_names = {t.name for t in registry.builtin_tools()}
        tools = registry.builtin_tools(exclude={"bash"})
        excluded_names = {t.name for t in tools}
        assert "bash" not in excluded_names
        assert excluded_names == all_names - {"bash"}

    def test_mcp_tools_empty(self):
        registry = ToolRegistry()
        assert registry.mcp_tools() == []
