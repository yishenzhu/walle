import asyncio
import subprocess

from ...infra import tool_context


async def bash(cmd: str = "") -> str:
    """Execute a bash command and return stdout+stderr.

    Args:
        cmd: Shell command string. E.g. 'ls -la'.
    """
    if not cmd:
        return "Error: cmd is required"

    # 在会话工作目录执行；无会话 cwd 则不设（子进程继承服务端 cwd）
    ctx = tool_context.get()
    cwd = ctx.cwd if ctx else None
    proc = await asyncio.create_subprocess_exec(
        "bash",
        "-c",
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode("utf-8", errors="replace")
