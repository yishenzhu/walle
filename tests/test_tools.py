"""Tool 测试。"""

import pytest

from ..core import ExtensionRunner
from ..infra import EventBus, Tool, tool_context


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


class TestRunnerToolTable:
    """会话工具表由 ExtensionRunner 持有：注册 / 覆盖 / 摘除 / 查询。"""

    @staticmethod
    def _tool(name: str) -> Tool:
        async def fn(args):
            return name

        return Tool(name=name, description=name, parameters={"type": "object"}, fn=fn)

    @pytest.fixture
    def runner(self):
        return ExtensionRunner(EventBus())

    def test_empty_tool_table(self, runner):
        assert runner.all_tools() == []

    def test_register_duplicate_replaces(self, runner):
        """同名后注册者覆盖先注册者（后到者胜）。"""
        first = self._tool("x")
        runner.register_tool(first)

        second = self._tool("x")
        runner.register_tool(second)

        tools = [t for t in runner.all_tools() if t.name == "x"]
        assert tools == [second]  # 旧实例被替换，只剩新实例

    def test_register_same_batch_duplicate_raises(self, runner):
        """同一批内工具名重复是编程错误（整批抛错）。"""
        with pytest.raises(ValueError, match="Duplicate tool name"):
            runner.register_tool(self._tool("a"), self._tool("a"))

    def test_remove_tool(self, runner):
        runner.register_tool(self._tool("x"))
        runner.remove_tool("x")
        assert runner.all_tools() == []
        runner.remove_tool("x")  # 不存在则忽略

    def test_all_tools_returns_registered(self, runner):
        async def custom_tool(x: str) -> str:
            """custom"""
            return x

        runner.register_tool(Tool.from_function(custom_tool))
        names = {t.name for t in runner.all_tools()}
        assert "custom_tool" in names
