"""本地工具沙箱：bubblewrap 命令构造 + 沙箱扩展（覆盖内置工具）。

沙箱语义：
- 整个文件系统只读（--ro-bind / /）
- 会话工作目录（cwd）单独 bind 可写——agent 在 cwd 里正常干活，
  cwd 之外（系统、HOME、其他项目）一律只读
- hidden_paths 用空视图遮罩：目录→空 tmpfs，文件→ /dev/null（读为空）
- network=False 时 --unshare-net 断网

形态：沙箱作为进程级扩展（main 里 extensions.add("sandbox", Sandbox().as_ext)），
同名覆盖内置工具（builtin 先激活、沙箱后激活 → 后到覆盖先到）。
配置随沙箱实例构造注入，不挂全局 conf；"是否沙箱"体现在装配哪个扩展。
沙箱依赖会话工作目录（cwd）：无 cwd 时沙箱工具直接报错，不回退。
"""

import asyncio
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .extension import ExtensionAPI
from .tool import Tool, tool_context

# 沙箱内默认隐藏的凭据路径（目录或文件均可，不存在则忽略）
DEFAULT_HIDDEN_PATHS = ["~/.ssh", "~/.netrc"]


@dataclass
class SandboxConfig:
    """沙箱策略：隐藏路径 + 网络开关。可写区由会话 cwd 派生，不在此配置。"""

    hidden_paths: list[str] = field(default_factory=lambda: list(DEFAULT_HIDDEN_PATHS))
    network: bool = True  # False → --unshare-net 断网


class Sandbox:
    """沙箱扩展：覆盖内置工具为沙箱实现（当前覆盖 bash，可扩展）。

    用法（main.py）：extensions.add("sandbox", Sandbox().as_ext)，
    须在 builtin 之后注册——会话激活时后到者同名覆盖先到者。
    """

    def __init__(self, config: SandboxConfig | None = None):
        self._config = config or SandboxConfig()

    def argv(self, cwd: str, cmd: str) -> list[str]:
        """构造沙箱执行 bash -c 的完整 argv。

        挂载序：全盘只读 → /dev /proc /tmp → cwd 可写 → 隐藏路径遮罩。
        隐藏路径：目录→空 tmpfs；文件→/dev/null（读为空）；宿主不存在→跳过。
        """
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

    async def as_ext(self, api: ExtensionAPI) -> None:
        """覆盖 bash：注册沙箱版（与 builtin 的裸 bash 同名，激活后到覆盖）。

        宿主机无 bwrap 时抛异常 → 扩展加载期标记 FAILED，自动跳过
        （会话仍用 builtin 裸 bash），实现优雅降级。
        """
        if shutil.which("bwrap") is None:
            raise RuntimeError(
                "bwrap 不可用：沙箱扩展跳过（apt install bubblewrap 可启用）"
            )

        async def bash(cmd: str = "") -> str:
            if not cmd:
                return "Error: cmd is required"
            ctx = tool_context.get()
            cwd = ctx.cwd if ctx is not None else None
            if not cwd:
                return "Error: 沙箱 bash 需要会话工作目录（cwd）"
            proc = await asyncio.create_subprocess_exec(
                *self.argv(cwd, cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            return stdout.decode("utf-8", errors="replace")

        api.register_tool(Tool.from_function(bash))
