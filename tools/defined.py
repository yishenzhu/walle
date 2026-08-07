"""模型定义工具的校验、加载与持久化。

模型通过 define_tool 提供 name/code：
- 静态分析（ast 语法 + pyflakes）拦语法/逻辑错误
- 契约校验：code 必须定义顶层 async def <name>，且必填 docstring（作为工具描述）
- importlib 加载函数 → 供 Tool.from_function 构造工具
- 持久化到 .agent/tools/<name>/code.py，启动时扫描恢复

安全模型：不限制 import（执行风险交给 ApprovalPolicy 审批）。
"""

from __future__ import annotations

import ast
import importlib.util
import logging
from pathlib import Path

from pyflakes.api import check
from pyflakes.reporter import Reporter

logger = logging.getLogger(__name__)


class ToolCodeError(ValueError):
    """动态工具代码校验失败。"""


class DefinedTool:
    """模型定义的代码工具：校验、持久化（.agent/tools/<name>/code.py）、加载。"""

    def __init__(self, root: Path | None = None):
        if root is None:
            from ..conf import DOT_AGENT

            root = DOT_AGENT / "tools"
        self._root = root

    @staticmethod
    def _validate_code(code: str, name: str) -> None:
        """静态分析 + 契约校验，失败抛 ToolCodeError。"""
        # 语法检查
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            raise ToolCodeError(f"语法错误: {e}") from e

        # 契约：顶层必须定义 async def <name>，且必填 docstring（作为工具描述）
        top_async_fns = [
            n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name
        ]
        if not top_async_fns:
            raise ToolCodeError(f"代码中未定义顶层 async def {name}() 作为工具入口")
        if not ast.get_docstring(top_async_fns[0]):
            raise ToolCodeError(f"async def {name}() 必须写 docstring（docstring 作为工具描述）")

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
        """从 .py 文件加载指定函数。"""
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

    def create(self, name: str, code: str) -> "Tool":
        """校验 + 保存 + 加载 + 构造 Tool，一步完成。失败抛 ToolCodeError/OSError。"""
        from ..tools.tool import Tool  # 延迟导入避免循环

        self._validate_code(code, name)
        path = self.dir_for(name) / "code.py"
        self.dir_for(name).mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        fn = self._load_from_file(str(path), name)
        return Tool.from_function(fn)

    def _codes(self) -> dict[str, str]:
        """扫描全部已持久化工具，返回 name -> code。"""
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

    def load(self) -> list["Tool"]:
        """加载全部已持久化工具为可注册的 Tool（单个失败跳过）。"""
        from ..tools.tool import Tool  # 延迟导入避免循环

        tools: list[Tool] = []
        for name, code in self._codes().items():
            try:
                tools.append(self.create(name, code))
            except Exception as e:
                logger.warning(f"defined tool load failed ({name}): {e}")
        return tools
