"""文件工具路径解析：相对路径按会话工作目录（cwd）解析。

与 bash / 沙箱保持一致：绝对路径原样使用，相对路径落到会话 cwd；
没有会话 cwd 时退回进程 cwd（测试/独立调用）。
"""

from pathlib import Path

from ...infra import tool_context


def resolve_tool_path(path: str) -> Path:
    p = Path(path).expanduser()
    if p.is_absolute():
        return p
    ctx = tool_context.get()
    cwd = ctx.cwd if ctx is not None else None
    if cwd:
        return Path(cwd).expanduser() / p
    return p
