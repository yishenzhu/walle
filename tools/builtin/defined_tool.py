"""模型定义工具的校验、加载与持久化。"""

import ast
import importlib.util
import logging
from pathlib import Path

from pyflakes.api import check
from pyflakes.reporter import Reporter

from ...conf import AGENT_DIR
from ...infra import Tool, tool_context

logger = logging.getLogger(__name__)


class ToolCodeError(ValueError):
    """动态工具代码校验失败。"""


class DefinedTool:
    """模型定义的代码工具：校验、持久化（.agent/tools/<name>/code.py）、加载。"""

    def __init__(self, root: Path | None = None):
        self._root = root or AGENT_DIR / "tools"

    @staticmethod
    def _validate_code(code: str, name: str) -> None:
        """Static analysis and contract validation; raises ToolCodeError."""
        # 语法检查
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            raise ToolCodeError(f"语法错误: {e}") from e

        # 契约：顶层必须定义 async def <name>，且必填 docstring（作为工具描述）
        top_async_fns = [
            n
            for n in tree.body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == name
        ]
        if not top_async_fns:
            raise ToolCodeError(f"代码中未定义顶层 async def {name}() 作为工具入口")
        if not ast.get_docstring(top_async_fns[0]):
            raise ToolCodeError(
                f"async def {name}() 必须写 docstring（docstring 作为工具描述）"
            )

        # pyflakes 静态分析（未定义名/未使用导入等）
        messages: list[str] = []

        class _Reporter(Reporter):
            def __init__(self):
                super().__init__(None, None)  # 不用流，直接收消息

            def unexpectedError(self, filename, msg):
                messages.append(f"internal: {msg}")

            def syntaxError(self, filename, msg, lineno, column, text):
                messages.append(f"语法: {msg}")

            def flake(self, message):
                messages.append(str(message))

        check(code, "defined_tool", _Reporter())
        if messages:
            raise ToolCodeError("; ".join(messages[:5]))

    @staticmethod
    def _load_from_file(path: str, fn_name: str):
        """Load a function from a .py file."""
        spec = importlib.util.spec_from_file_location("defined_tool", path)
        if spec is None or spec.loader is None:
            raise ToolCodeError(f"无法加载模块: {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, fn_name, None)
        if fn is None:
            raise ToolCodeError(f"模块中找不到函数 {fn_name}")
        return fn

    def dir_for(self, name: str) -> Path:
        return self._root / name

    def create(self, name: str, code: str) -> Tool:
        """Validate, save, load, and build a Tool. Raises ToolCodeError/OSError."""
        self._validate_code(code, name)
        path = self.dir_for(name) / "code.py"
        self.dir_for(name).mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        fn = self._load_from_file(str(path), name)
        return Tool.from_function(fn)

    def _read_codes(self) -> dict[str, str]:
        """Scan persisted tools and return name -> code."""
        result: dict[str, str] = {}
        if not self._root.exists():
            return result
        for d in sorted(self._root.iterdir()):
            p = d / "code.py"
            if d.is_dir() and p.exists():
                try:
                    result[d.name] = p.read_text(encoding="utf-8")
                except OSError as e:
                    logger.warning(f"defined tool load failed ({d.name}): {e}")
        return result

    def load(self) -> list[Tool]:
        """Load persisted tools as registrable Tools (skip broken ones)."""
        tools: list[Tool] = []
        for name, code in self._read_codes().items():
            try:
                tools.append(self.create(name, code))
            except Exception as e:
                logger.warning(f"defined tool load failed ({name}): {e}")
        return tools


async def define_tool(name: str = "", code: str = "") -> str:
    """Define a new tool from code (top-level `async def <name>` with a docstring).
    Validated tools are persisted and registered in the current session."""
    try:
        tool = DefinedTool().create(name, code)
    except (ToolCodeError, OSError, ValueError) as e:
        return f"定义失败: {e}"
    ctx = tool_context.get()
    if ctx is None or ctx.tool_table is None:
        return f"定义失败: 当前无会话注册通道（{name} 已持久化）"
    ctx.tool_table.add(tool)
    return f"工具已定义并生效: {name}"
