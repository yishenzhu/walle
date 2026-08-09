"""jupyter 工具（Jupyter kernel）测试。"""
import pytest

from ..infra import PyKernel
from ..tools.builtin.python import jupyter
from ..tools.tool import ToolContext, tool_context


@pytest.fixture
async def kernel():
    """每个测试一个独立 PyKernel 实例（kernel 随之隔离）。"""
    k = PyKernel()
    yield k
    await k.close()


async def test_state_persists_across_calls(kernel):
    """跨多次调用保留状态：x 定义后下次可用（CodeAct 核心特性）。"""
    assert await kernel.run("x = 41") == "(no output)"
    assert await kernel.run("x + 1") == "42"


async def test_import_persists(kernel):
    """import 跨调用保留。"""
    await kernel.run("import math")
    out = await kernel.run("math.sqrt(16)")
    assert out == "4.0"


async def test_error_returns_traceback(kernel):
    """异常返回完整 traceback 供模型 self-debug。"""
    out = await kernel.run("1 / 0")
    assert out.startswith("Error:")
    assert "ZeroDivisionError" in out


async def test_jupyter_uses_context_kernel(kernel):
    """jupyter 工具从 ToolContext 获取 kernel，按会话隔离执行。"""
    ctx = ToolContext(kernel=kernel)
    token = tool_context.set(ctx)
    try:
        assert await jupyter("x = 41") == "(no output)"
        assert await jupyter("x + 1") == "42"
    finally:
        tool_context.reset(token)


async def test_jupyter_no_kernel_in_context():
    """上下文无 kernel 时返回明确错误。"""
    ctx = ToolContext(kernel=None)
    token = tool_context.set(ctx)
    try:
        out = await jupyter("1 + 1")
        assert "no python kernel" in out
    finally:
        tool_context.reset(token)


async def test_kernel_start_and_close():
    """kernel 生命周期：start 后可执行，close 后重新 start 可用。"""
    k = PyKernel()
    await k.start()
    assert await k.execute("2 + 2", timeout=30) == "4"
    await k.close()
    await k.start()
    assert await k.execute("3 * 3", timeout=30) == "9"
    await k.close()
