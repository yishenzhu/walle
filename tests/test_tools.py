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
    @pytest.fixture
    async def registry(self, tmp_path, monkeypatch):
        """已初始化（含 python kernel 预启动）的 ToolRegistry。"""
        from ..conf import Config, LogConfig

        monkeypatch.setattr("walle.conf.DOT_AGENT", tmp_path)
        conf = Config(
            log=LogConfig(level="INFO", path="x.log", backup_count=1),
        )
        reg = ToolRegistry()
        await reg.initialize(conf)
        yield reg
        await reg.close()

    async def test_builtin_tools_loaded(self, registry):
        names = {t.name for t in registry.all_tools()}
        assert "bash" in names
        assert "ask_user" in names
        assert "jupyter" in names

    async def test_add_function_duplicate_raises(self):
        registry = ToolRegistry()

        async def bash(cmd: str = "") -> str:
            """bash"""
            return ""

        registry.add_function(bash)
        with pytest.raises(ValueError, match="Duplicate tool name"):
            registry.add_function(bash)

    async def test_add_function_new(self):
        registry = ToolRegistry()

        async def custom_tool(x: str) -> str:
            """custom"""
            return x

        registry.add_function(custom_tool)
        names = {t.name for t in registry.all_tools()}
        assert "custom_tool" in names

    async def test_mcp_empty(self, registry):
        assert {"bash", "ask_user", "define_tool", "jupyter"} <= {
            t.name for t in registry.all_tools()
        }

    async def test_initialize_registers_python_tool(self, registry):
        """initialize 后 jupyter 工具已注册（纯函数，kernel 由 Runner 经 ToolContext 提供）。"""
        py_tool = next(t for t in registry.all_tools() if t.name == "jupyter")
        assert py_tool is not None
        assert "code" in py_tool.parameters["properties"]
