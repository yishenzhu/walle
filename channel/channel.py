"""Channel 协议与 CLI 通道实现。

Channel 是唯一稳定契约：Runner / Executor / 工具只认识 notify（广播）与
call（点对点）两个原语，实现可替换（CLI / 飞书 / 测试）。
"""
import asyncio
import signal
from typing import Any, Protocol, runtime_checkable

from ..schemas import (
    ApprovalRsp,
    Delta,
    NotificationUnion,
    Receive,
    Inquiry,
    Approval,
    ServiceUnion,
    UserInput,
)
from .render import render_notification


@runtime_checkable
class Channel(Protocol):
    async def notify(self, notification: NotificationUnion) -> None: ...  # 广播，无返回
    async def call(self, service: ServiceUnion) -> Any: ...               # 点对点，有返回


class CLIChannel:
    """CLI 通道：直接实现 Channel。notify → 渲染；call → 终端交互。

    终止控制（Ctrl+C 信号驱动）：
      - 执行中按 Ctrl+C → 取消当前 run，回到输入提示
      - 空闲等待输入时按 Ctrl+C → KeyboardInterrupt → 退出程序
    """

    def __init__(self) -> None:
        self._cancel_event = asyncio.Event()

    # ── 终止控制 ────────────────────────────────────────
    async def run_interruptible(self, coro) -> bool:
        """运行协程直到完成或被 Ctrl+C 打断；返回 True 表示被打断。

        仅 run 期间拦截 SIGINT（取消 run）；结束后 remove 恢复默认 handler，
        空闲时 Ctrl+C 走默认 KeyboardInterrupt → receive 捕获 → 退出。
        """
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(coro)
        loop.add_signal_handler(signal.SIGINT, self._cancel_event.set)
        try:
            cancel_waiter = asyncio.create_task(self._cancel_event.wait())
            done, pending = await asyncio.wait(
                [task, cancel_waiter],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            return task not in done
        finally:
            loop.remove_signal_handler(signal.SIGINT)
            self._cancel_event.clear()

    # ── 通知：广播渲染（无返回）──
    async def notify(self, n: NotificationUnion) -> None:
        text = render_notification(n)
        # CLI 表现层：Delta 流式增量不换行，其余换行
        print(text, end="" if isinstance(n, Delta) else "\n", flush=True)

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
        """读用户输入（去首尾空白）。

        - 直接回车 / 全空格 → content=""（main break 退出）
        - Ctrl+C / EOF → content=None（main break 退出）
        """
        try:
            content = await asyncio.to_thread(input, "You> ")
        except (EOFError, KeyboardInterrupt):
            return UserInput()  # Ctrl+C / EOF：退出（content=None）
        return UserInput(content=content.strip())

    async def inquiry(self, question: str, options: list[str] | None = None) -> str:
        print(f"  [提问] {question}")
        if options:
            for i, opt in enumerate(options, 1):
                print(f"    {i}. {opt}")
        answer = await asyncio.to_thread(input, "  回答: ")
        return answer.strip()

    async def ask_approval(self, tool_name: str, arguments: dict) -> ApprovalRsp:
        print(f"  [审批请求] 允许执行: {tool_name}({arguments})?")
        while True:
            answer = (await asyncio.to_thread(input, "  允许? (y/n): ")).strip().lower()
            if answer in ("y", "yes"):
                return ApprovalRsp(approved=True)
            if answer in ("n", "no"):
                reason = await asyncio.to_thread(input, "  拒绝原因(可选): ")
                return ApprovalRsp(approved=False, reason=reason.strip() or None)
            print("  请输入 y/n")
