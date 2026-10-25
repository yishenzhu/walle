"""bash 工具：执行 shell 命令（沙箱版复用其 run_command 执行原语）。"""

import asyncio
import os
import signal
import subprocess

from ...infra import tool_context

# 正常路径 executor 超时（conf 默认 30s）会先取消本协程（CancelledError）；
# 此值仅作逃生舱：executor 被豁免 / 后台作业无超时时兜底。
COMMAND_TIMEOUT = 300.0  # 秒


async def run_command(
    argv: list[str],
    *,
    cwd: str | None = None,
    timeout: float = COMMAND_TIMEOUT,
) -> str:
    """Run a subprocess (full argv) and return stdout+stderr."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # 独立进程组：超时/取消时可整组清理（bash -c 再起的子孙进程一并带走）
        start_new_session=True,
    )
    try:
        out = await asyncio.wait_for(proc.communicate(), timeout)
    except (TimeoutError, asyncio.CancelledError) as exc:
        # executor 的 wait_for 超时取消本协程时，这里负责杀掉进程组防孤儿
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            # 进程组已不存在（可能只剩单进程）：直接杀进程本身
            try:
                proc.kill()
            except ProcessLookupError:
                pass  # 进程恰好已退出
        await proc.communicate()  # 回收，避免僵尸
        if isinstance(exc, asyncio.TimeoutError):
            return f"Error: command timed out after {timeout:.0f}s (killed)"
        raise

    stdout = out[0]
    return stdout.decode("utf-8", errors="replace")


async def bash(cmd: str = "") -> str:
    """Execute a bash command and return stdout+stderr.
    `cmd` is a shell command string; runs in the session cwd."""
    if not cmd:
        return "Error: cmd is required"

    # 在会话工作目录执行；无会话 cwd 则不设（子进程继承服务端 cwd）
    ctx = tool_context.get()
    cwd = ctx.cwd if ctx else None
    return await run_command(["bash", "-c", cmd], cwd=cwd)
