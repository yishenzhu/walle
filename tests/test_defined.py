import asyncio

from ..tools.builtin.defined import DefinedTool, ToolCodeError

from .conftest import make_tool_context


def test_create_ok(tmp_path):
    """create 校验通过并返回可运行的 Tool。"""
    tool = DefinedTool(tmp_path).create(
        "add",
        "async def add(a: int, b: int) -> int:\n    \"\"\"加法工具\"\"\"\n    return a + b\n",
    )
    assert tool.name == "add"
    assert asyncio.run(tool.run({"a": 1, "b": 2})) == 3


def test_create_missing_fn(tmp_path):
    code = "async def other(a: int) -> int:\n    \"\"\"other\"\"\"\n    return a\n"
    try:
        DefinedTool(tmp_path).create("add", code)
        assert False, "应抛错"
    except ToolCodeError as e:
        assert "async def add" in str(e)


def test_create_missing_docstring(tmp_path):
    code = "async def add(a: int, b: int) -> int:\n    return a + b\n"
    try:
        DefinedTool(tmp_path).create("add", code)
        assert False, "应抛错"
    except ToolCodeError as e:
        assert "docstring" in str(e)


def test_create_syntax_error(tmp_path):
    try:
        DefinedTool(tmp_path).create("add", "async def add(:\n")
        assert False, "应抛错"
    except ToolCodeError as e:
        assert "语法错误" in str(e)


def test_load_restore(tmp_path):
    """create 持久化 → load 恢复为工具。"""
    root = tmp_path / "tools"
    store = DefinedTool(root)
    code = "async def greet(name: str) -> str:\n    \"\"\"问候工具\"\"\"\n    return f'hi {name}'\n"
    store.create("greet", code)

    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].name == "greet"
    assert asyncio.run(loaded[0].run({"name": "walle"})) == "hi walle"


async def test_define_tool_registers_via_context(tmp_path, monkeypatch):
    """define_tool 工具执行时经 tool_context 的注册通道把新工具加入会话。"""
    from ..core import ExtensionRunner
    from ..infra import EventBus, ToolContext, tool_context
    from ..tools.builtin.defined import define_tool

    monkeypatch.setattr("walle.tools.builtin.defined.DOT_AGENT", tmp_path)

    runner = ExtensionRunner(EventBus())
    ctx = make_tool_context(ext=runner)
    token = tool_context.set(ctx)
    try:
        out = await define_tool(
            name="double",
            code='async def double(n: int) -> int:\n    """翻倍工具"""\n    return n * 2\n',
        )
    finally:
        tool_context.reset(token)

    assert "已定义并生效" in out
    names = {t.name for t in runner.all_tools()}
    assert "double" in names  # 已注册进会话工具表
    # 定义的工具可运行
    double = next(t for t in runner.all_tools() if t.name == "double")
    assert await double.run({"n": 21}) == 42
