"""本地工具沙箱：bubblewrap 命令构造 + 沙箱插件（覆盖内置工具）。"""

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.tool import Tool, tool_context
from ..plugin import Plugin
from ..spec import ToolService
from .builtin.bash import run_command

# 沙箱内默认隐藏的凭据路径（目录或文件均可，不存在则忽略）
DEFAULT_HIDDEN_PATHS = ["~/.ssh", "~/.netrc"]


@dataclass
class SandboxConfig:
    """沙箱策略：隐藏路径 + 网络开关。可写区由会话 cwd 派生，不在此配置。"""

    hidden_paths: list[str] = field(default_factory=lambda: list(DEFAULT_HIDDEN_PATHS))
    network: bool = True  # False → --unshare-net 断网


class Sandbox:
    """沙箱插件：覆盖内置工具为沙箱实现（当前覆盖 bash，可扩展）。"""

    def __init__(self, config: SandboxConfig | None = None):
        self._config = config or SandboxConfig()

    def argv(self, cwd: str, cmd: str) -> list[str]:
        """Build the full argv for sandboxed bash -c."""
        cfg = self._config
        args = ["bwrap", "--ro-bind", "/", "/"]
        # 基础伪文件系统先挂（--tmpfs /tmp 等会覆盖 /tmp 下路径，须先于 cwd bind）
        args += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
        # 会话工作目录可写（覆盖只读层）
        args += ["--bind", cwd, cwd]
        # 隐藏路径：空视图覆盖原内容（最后挂载，优先级最高）
        for h in cfg.hidden_paths:
            p = Path(h).expanduser()
            if p.is_dir():
                args += ["--tmpfs", str(p)]
            elif p.exists():
                args += ["--bind", "/dev/null", str(p)]
            # 宿主不存在：无需遮罩
        if not cfg.network:
            args += ["--unshare-net"]
        return [*args, "bash", "-c", cmd]

    def _bash(self):
        """Build the sandboxed bash tool."""
        async def bash(cmd: str = "") -> str:
            """Execute a bash command in the sandbox and return stdout+stderr."""
            if not cmd:
                return "Error: cmd is required"
            session = tool_context.get()
            cwd = session.cwd if session is not None else None
            if not cwd:
                return "Error: 沙箱 bash 需要会话工作目录（cwd）"
            # 复用内置 bash 的执行原语（超时 killpg + 输出上限），argv 直传不经 shell
            return await run_command(self.argv(cwd, cmd))

        return Tool.from_function(bash)

    def plugin(self) -> Plugin:
        """Return the sandbox plugin: inject ToolService and override bash."""
        return Plugin(
            name="sandbox",
            apply=self._apply,
            inject=(ToolService,),
        )

    async def _apply(self, ctx, config) -> None:
        """Override bash with the sandboxed version (same name; last wins)."""
        if shutil.which("bwrap") is None:
            raise RuntimeError(
                "bwrap 不可用：沙箱插件跳过（apt install bubblewrap 可启用）"
            )
        ctx.get(ToolService).add(self._bash())
