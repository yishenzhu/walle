import asyncio

from ..tools.defined import DefinedTool, ToolCodeError


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
