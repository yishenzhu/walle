import asyncio
import subprocess


async def bash(cmd: str = "") -> str:
    """Execute a bash command and return stdout+stderr.

    Args:
        cmd: Shell command string. E.g. 'ls -la'.
    """
    if not cmd:
        return "Error: cmd is required"

    proc = await asyncio.create_subprocess_exec(
        "bash",
        "-c",
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode("utf-8", errors="replace")
