import asyncio
import tempfile
from pathlib import Path

from ..conf import DOT_AGENT
from ..tools.dynamic import DynamicToolStore, ToolCodeError, load_from_file, validate_code


def test_validate_ok():
    code = "async def add(a: int, b: int) -> int:\n    \"\"\"加法工具\"\"\"\n    return a + b\n"
    validate_code(code, "add")  # 不抛错


def test_validate_missing_fn():
    code = "async def other(a: int) -> int:\n    \"\"\"other\"\"\"\n    return a\n"
    try:
        validate_code(code, "add")
        assert False, "应抛错"
    except ToolCodeError as e:
        assert "async def add" in str(e)


def test_validate_missing_docstring():
    code = "async def add(a: int, b: int) -> int:\n    return a + b\n"
    try:
        validate_code(code, "add")
        assert False, "应抛错"
    except ToolCodeError as e:
        assert "docstring" in str(e)


def test_validate_syntax_error():
    try:
        validate_code("async def add(:\n", "add")
        assert False, "应抛错"
    except ToolCodeError as e:
        assert "语法错误" in str(e)


def test_load_from_file_and_restore(tmp_path):
    root = tmp_path / "tools"
    store = DynamicToolStore(root)
    code = "async def greet(name: str) -> str:\n    \"\"\"问候工具\"\"\"\n    return f'hi {name}'\n"
    store.save("greet", code)

    fn = load_from_file(str(root / "greet" / "code.py"), "greet")
    assert fn.__name__ == "greet"

    loaded = store.load_all()
    assert "greet" in loaded and loaded["greet"] == code
