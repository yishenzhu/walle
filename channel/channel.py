"""Channel 协议与 CLI 通道实现。

Channel 是唯一稳定契约：Runner / Executor / 工具只认识 notify（广播）与
call（点对点）两个原语，实现可替换（CLI / 飞书 / 测试）。
"""
import asyncio
import signal
from typing import Any, Protocol, runtime_checkable

from ..schemas import (
    ApprovalResponse,
    Delta,
    DeltaEnd,
    Error,
    NotificationUnion,
    Receive,
    Inquiry,
    Approval,
    ServiceUnion,
    ToolResult,
    ToolStart,
    UserInput,
)


@runtime_checkable
class Channel(Protocol):
    async def notify(self, notification: NotificationUnion) -> None: ...  # 广播，无返回
    async def call(self, service: ServiceUnion) -> Any: ...               # 点对点，有返回


class CLIChannel:
    """CLI 通道：直接实现 Channel。notify → 渲染；call → 终端交互。

    终止控制（Ctrl+C 信号驱动，业界一致做法）：
      - agent 执行中按 Ctrl+C → 取消当前 run，回到输入提示
      - 空闲等待输入时按 Ctrl+C → KeyboardInterrupt → 退出程序
    统一出口为 Ctrl+C，无 :q 命令。
    """

    def __init__(self) -> None:
        self._cancel_event = asyncio.Event()

    # ── 终止控制 ────────────────────────────────────────
    def _on_sigint(self, sig, frame) -> None:
        """Ctrl+C：标记取消请求（仅当 run 执行中由 run_with_terminate 消费）。"""
        self._cancel_event.set()

    async def run_with_terminate(self, task: asyncio.Task) -> bool:
        """运行 task 直到完成或被 Ctrl+C 打断；返回 True 表示被打断。

        - run 完成后立即返回，主循环回到 receive()；
        - 此时 Ctrl+C 由 Python 默认处理 → KeyboardInterrupt → 退出程序。
        - task 取消为 fire-and-forget（不等清理），interrupted 时由调用方决定后续。
        """
        cancel_waiter = asyncio.create_task(self._cancel_event.wait())
        done, pending = await asyncio.wait(
            [task, cancel_waiter],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()  # 清理未触发的 waiter，防泄漏
        self._cancel_event.clear()
        interrupted = task not in done
        if interrupted:
            task.cancel()  # Ctrl+C：取消当前 run（fire-and-forget）
        return interrupted

    # ── 通知：广播渲染（无返回）──
    async def notify(self, n: NotificationUnion) -> None:
        match n:
            case Delta(delta=delta):
                print(delta, end="", flush=True)
            case DeltaEnd():
                print()
            case ToolStart(tool_name=name, arguments=args):
                print(f"  [调用工具 {name}({args})]", flush=True)
            case ToolResult(tool_call_id=tc_id, result=result, error=None):
                print(f"  [工具结果 {tc_id}] {truncate(result, 512)}", flush=True)
            case ToolResult(tool_call_id=tc_id, error=err):
                print(f"  [工具错误 {tc_id}] {err}", flush=True)
            case Error(message=msg):
                print(f"  [错误] {msg}", flush=True)

    # ── 服务：终端交互（有返回）──
    async def call(self, s: ServiceUnion) -> Any:
        match s:
            case Receive():
                return await self.receive()
            case Inquiry(question=q, options=opts):
                return await self.inquiry(q, opts)
            case Approval(tool_name=n, arguments=a):
                return await self.ask_approval(n, a)

    async def receive(self) -> UserInput:
        try:
            content = await asyncio.to_thread(input, "You> ")
        except (EOFError, KeyboardInterrupt):
            content = ""
        return UserInput(content=content)

    async def inquiry(self, question: str, options: list[str] | None = None) -> str:
        print(f"  [提问] {question}")
        if options:
            for i, opt in enumerate(options, 1):
                print(f"    {i}. {opt}")
        answer = await asyncio.to_thread(input, "  回答: ")
        return answer.strip()

    async def ask_approval(self, tool_name: str, arguments: dict) -> ApprovalResponse:
        print(f"  [审批请求] 允许执行: {tool_name}({arguments})?")
        while True:
            answer = (await asyncio.to_thread(input, "  允许? (y/n): ")).strip().lower()
            if answer in ("y", "yes"):
                return ApprovalResponse(approved=True)
            if answer in ("n", "no"):
                reason = await asyncio.to_thread(input, "  拒绝原因(可选): ")
                return ApprovalResponse(approved=False, reason=reason.strip() or None)
            print("  请输入 y/n")

    # ── 生命周期 ────────────────────────────────────────
    async def start(self) -> None:
        # 仅主线程可装信号 handler；事件循环在主线程则安全
        signal.signal(signal.SIGINT, self._on_sigint)

    async def close(self) -> None: ...


def truncate(value: Any, limit: int) -> str:
    s = str(value)
    return s if len(s) <= limit else s[:limit] + "..."
