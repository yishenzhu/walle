"""分层依赖约束测试（架构原则的可执行门禁）。

docs/architecture.md 的「协议优先分层」此前只写在文档里，无任何机制阻止
漂移。本测试用 AST 静态检查导入方向，把原则变成 CI 会拦下的断言：

1. spec/ 是叶子：不得 import 任何其他层（协议面不依赖实现）。
2. 实现层不得反向 import core（依赖方向单向：实现 → 协议/基建）。
3. 引擎（runner/executor/agent）不得 import 具体注入件（messages 存储、
   infra.provider）——它们必须经 spec 协议由装配根注入。
   core/session.py 是会话装配根，按设计可 new 存储，故豁免。
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent  # 仓库根 = walle 包目录
LAYERS = ("spec", "infra", "messages", "core", "tools", "channel", "conf")


def _iter_py_files(layer: str):
    for path in (ROOT / layer).rglob("*.py"):
        if "__pycache__" not in path.parts:
            yield path


def _package_of(path: Path) -> str:
    """文件所在包的点分路径（相对 walle 包），如 spec/protocol/view.py → walle.spec.protocol。"""
    rel = path.relative_to(ROOT).with_suffix("")
    parts = rel.parts[:-1]  # 去掉模块名，留包路径
    return ".".join(("walle", *parts))


def _resolved_module(node: ast.ImportFrom, package: str) -> str | None:
    """把 ImportFrom 解析为绝对点分模块名；非相对导入返回 node.module 或 None。"""
    if node.level == 0:
        return node.module
    parts = package.split(".")
    if node.level > len(parts):
        return None
    base = parts[: len(parts) - (node.level - 1)]
    if node.module:
        base = [*base, *node.module.split(".")]
    return ".".join(base)


def _imports_of(path: Path) -> list[str]:
    """该文件 import 的全部绝对模块名（含只在 `from x import` 中的 x）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _package_of(path)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolved_module(node, package)
            if resolved:
                modules.append(resolved)
    return modules


def _layer_of(module: str) -> str | None:
    """绝对模块名对应的顶层层名（walle.infra.tool → infra）；非 walle 包返回 None。"""
    parts = module.split(".")
    if len(parts) >= 2 and parts[0] == "walle":
        return parts[1]
    return None


def _violations(path: Path, forbidden) -> list[str]:
    bad = []
    for module in _imports_of(path):
        reason = forbidden(module)
        if reason:
            bad.append(f"{path.relative_to(ROOT)} imports {module} ({reason})")
    return bad


def test_spec_is_leaf():
    """协议面不依赖任何实现层：spec 只能 import 自己或第三方。"""
    problems = []
    for path in _iter_py_files("spec"):
        problems += _violations(
            path,
            lambda m: f"spec 是叶子，不能依赖 {_layer_of(m)}"
            if _layer_of(m) in (*LAYERS[1:],)
            else None,
        )
    assert problems == [], "分层违规（spec 必须保持叶子）:\n" + "\n".join(problems)


@pytest.mark.parametrize("layer", ("infra", "messages", "tools", "channel", "conf"))
def test_no_upward_import_into_core(layer):
    """实现层不得反向依赖 core：依赖方向单向，避免环。"""
    problems = []
    for path in _iter_py_files(layer):
        problems += _violations(
            path,
            lambda m: "实现层不能反向依赖 core" if _layer_of(m) == "core" else None,
        )
    assert problems == [], f"分层违规（{layer} 反向依赖 core）:\n" + "\n".join(problems)


def test_schemas_does_not_import_protocol():
    """spec 内部方向唯一：protocol → schemas。

    schemas 反引 protocol 会在导入期成环（protocol 尚未初始化完即被回引），
    也会让"数据模型"承载能力对象(如 Messages)。事件载荷因此只允许值类型。
    """
    problems = []
    for path in _iter_py_files("spec/schemas"):
        problems += _violations(
            path,
            lambda m: "schemas 不能反向依赖 protocol（成环）"
            if m.startswith("walle.spec.protocol")
            else None,
        )
    assert problems == [], "分层违规（spec.schemas 反引 protocol）:\n" + "\n".join(problems)


@pytest.mark.parametrize("name", ("runner.py", "executor.py", "agent.py"))
def test_engine_has_no_concrete_injectables(name):
    """引擎不得 import 具体注入件；存储与模型必须经 spec 协议由装配根注入。"""
    path = ROOT / "core" / name

    def forbidden(module: str) -> str | None:
        if _layer_of(module) == "messages":
            return "存储须经 spec.Messages 注入，不能 import 具体实现"
        if module.startswith("walle.infra.provider"):
            return "模型须经 spec.LLM 注入，不能 import 具体 provider"
        return None

    problems = _violations(path, forbidden)
    assert problems == [], f"分层违规（{name} 依赖具体注入件）:\n" + "\n".join(problems)
