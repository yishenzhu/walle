"""Jupyter kernel 基础设施：持久 Python 解释器（CodeAct 范式）。

PyKernel 管理一个 Jupyter kernel 子进程：状态跨多次调用保留
（变量 / import 持续有效），异常以 traceback 形式返回供模型 self-debug。
作为会话级计算资源挂在 ToolContext 上，由 Runner 创建/回收。
"""

import asyncio
import logging

from jupyter_client import AsyncKernelManager
from jupyter_client.asynchronous.client import AsyncKernelClient

logger = logging.getLogger(__name__)

MAX_OUTPUT = 8192  # 截断超长输出，防撑爆上下文


class PyKernel:
    """一个持久的 Jupyter kernel：状态跨回合保留，独立子进程隔离。"""

    def __init__(self, kernel_name: str = "python3", timeout: float = 30.0):
        self._kernel_name = kernel_name
        self._timeout = timeout
        self._km: AsyncKernelManager | None = None
        self._kc: AsyncKernelClient | None = None
        self._lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        async with self._start_lock:  # 防并发重复启动
            if self._started:
                return
            km = AsyncKernelManager(kernel_name=self._kernel_name)
            await km.start_kernel()
            kc = km.client()  # type: ignore[assignment]
            kc.start_channels()
            await kc.wait_for_ready(timeout=60)
            self._km, self._kc = km, kc
            self._started = True
            logger.info(f"python kernel started: {self._kernel_name}")

    async def execute(self, code: str, timeout: float) -> str:
        """在 kernel 中执行代码，返回统一文本结果（成功输出 / 失败 traceback）。"""
        await self.start()
        assert self._kc is not None
        async with self._lock:  # 串行执行，防交错污染状态
            msg_id = self._kc.execute(code)
            return await self._collect(msg_id, timeout)

    async def _collect(self, msg_id: str, timeout: float) -> str:
        """收集执行输出直到 idle：stdout/stderr 合并，异常返回完整 traceback。"""
        assert self._kc is not None
        output: list[str] = []
        error_tb: str | None = None
        deadline = asyncio.get_event_loop().time() + timeout

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return "Error: python execution timed out"
            try:
                msg = await asyncio.wait_for(self._kc.get_iopub_msg(), remaining)
            except asyncio.TimeoutError:
                return "Error: python execution timed out"
            msg_type = msg["header"]["msg_type"]
            content = msg["content"]
            if msg_type == "stream":
                output.append(content.get("text", ""))
            elif msg_type == "execute_result":
                text = content.get("data", {}).get("text/plain", "")
                output.append(text)
            elif msg_type == "error":
                error_tb = "\n".join(content.get("traceback", []))
            elif msg_type == "status" and content.get("execution_state") == "idle":
                break

        if error_tb is not None:
            return f"Error:\n{error_tb[:MAX_OUTPUT]}"
        text = "".join(output).strip()
        if not text:
            return "(no output)"
        return text[:MAX_OUTPUT]

    async def close(self) -> None:
        async with self._start_lock:
            if self._kc is not None:
                self._kc.stop_channels()
            if self._km is not None:
                await self._km.shutdown_kernel(now=True)
            self._started = False
            logger.info("python kernel stopped")

    async def run(self, code: str = "") -> str:
        """在 kernel 中执行代码，状态跨多次调用保留（变量 / import 持续有效）。"""
        if not code:
            return "Error: code is required"
        try:
            return await self.execute(code, timeout=self._timeout)
        except Exception as e:
            logger.warning(f"python tool failed: {e}")
            return f"Error: python interpreter failed: {e}"
