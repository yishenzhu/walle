"""Tool 与 ToolRegistry 测试。"""

import pytest

from ..infra import Tool
from ..tools import ToolRegistry
from ..infra import ToolContext, tool_context


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
    def registry(self):
        """空 ToolRegistry（无任何本地工具）。"""
        return ToolRegistry()

    def test_empty_registry_has_no_local_tools(self, registry):
        """纯容器初始为空（内置工具由扩展系统激活进会话后才有）。"""
        assert registry.all_tools() == []

    async def test_add_tool_duplicate_replaces(self):
        """同名后注册者覆盖先注册者（后到者胜）。"""
        registry = ToolRegistry()
        first = self._tool("x")
        registry.add_tool(first)

        second = self._tool("x")
        registry.add_tool(second)

        tools = [t for t in registry.all_tools() if t.name == "x"]
        assert tools == [second]  # 旧实例被替换，只剩新实例

    async def test_add_tool_same_batch_duplicate_raises(self):
        """同一批内工具名重复是编程错误（整批抛错）。"""
        registry = ToolRegistry()
        with pytest.raises(ValueError, match="Duplicate tool name"):
            registry.add_tool(self._tool("a"), self._tool("a"))

    async def test_remove_tool(self):
        registry = ToolRegistry()
        registry.add_tool(self._tool("x"))
        registry.remove_tool("x")
        assert registry.all_tools() == []
        registry.remove_tool("x")  # 不存在则忽略

    async def test_all_tools_returns_added(self):
        registry = ToolRegistry()

        async def custom_tool(x: str) -> str:
            """custom"""
            return x

        registry.add_tool(Tool.from_function(custom_tool))
        names = {t.name for t in registry.all_tools()}
        assert "custom_tool" in names
