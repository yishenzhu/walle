"""jupyter 工具：从执行上下文取 kernel，执行 Python 代码（CodeAct 范式）。

kernel（PyKernel）是基础设施层（infra/jupyter.py）提供的会话级计算资源，
挂在 ToolContext 上，由 Runner 创建/回收；本模块只提供工具函数。
"""

from ..tool import tool_context


async def jupyter(code: str = "") -> str:
    """执行一段 Python 代码，解释器状态跨多次调用保留（变量 / import 持续有效）。

    适合多步计算、数据处理、组合多个工具结果、写脚本验证逻辑。
    代码异常时返回完整 traceback，可根据报错修改代码重试（self-debug）。
    Args:
        code: 要执行的 Python 代码。
    """
    ctx = tool_context.get()
    kernel = ctx.kernel if ctx is not None else None
    if kernel is None:
        return "Error: no python kernel in context"
    return await kernel.run(code)
