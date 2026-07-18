import asyncio
from typing import Protocol, runtime_checkable

from ..schemas import TextDeltaEnd, ToolResult

from ..schemas import (
    TextDelta,
    ToolCall,
    UserInput,
    InjectionInput,
    ApprovalResponse,
)


@runtime_checkable
class Channel(Protocol):
    async def send(
        self, event: TextDelta | TextDeltaEnd | ToolCall | ToolResult
    ) -> None: ...

    async def receive(self) -> UserInput: ...

    async def inquiry(self, question: str, options: list[str] | None = None) -> str: ...

    async def ask_approval(
        self,
        tool_name: str,
        arguments: dict,
    ) -> ApprovalResponse: ...

    def injections(self) -> list[InjectionInput]: ...

    def inject(self, content: str) -> None: ...


class CLIChannel:
    def __init__(self) -> None:
        self._injection_queue: asyncio.Queue[InjectionInput] = asyncio.Queue()

    async def send(
        self, event: TextDelta | TextDeltaEnd | ToolCall | ToolResult
    ) -> None:
        match event:
            case TextDelta(delta=delta):
                print(delta, end="", flush=True)
            case TextDeltaEnd():
                print()
            case ToolCall(tool_name=name, arguments=args):
                print(f"  [调用工具 {name}({args})]", flush=True)
            case ToolResult(tool_call_id=tool_call_id, result=result):
                result_str = str(result)
                if len(result_str) > 512:
                    result_str = result_str[:512] + "..."
                print(f"  [工具结果 {tool_call_id}] {result_str}", flush=True)

    async def receive(self) -> UserInput:
        try:
            content = await asyncio.to_thread(input, "You> ")
        except (EOFError, KeyboardInterrupt):
            content = ":q"
        return UserInput(content=content)

    async def inquiry(self, question: str, options: list[str] | None = None) -> str:
        print(f"  [提问] {question}")
        if options:
            for i, opt in enumerate(options, 1):
                print(f"    {i}. {opt}")
        answer = await asyncio.to_thread(input, "  回答: ")
        return answer.strip()

    async def ask_approval(
        self,
        tool_name: str,
        arguments: dict,
    ) -> ApprovalResponse:
        print(f"  [审批请求] 允许执行: {tool_name}({arguments})?")
        while True:
            answer = await asyncio.to_thread(input, "  允许? (y/n): ")
            answer = answer.strip().lower()
            if answer in ("y", "yes"):
                return ApprovalResponse(approved=True)
            if answer in ("n", "no"):
                reason = await asyncio.to_thread(input, "  拒绝原因(可选): ")
                return ApprovalResponse(approved=False, reason=reason.strip() or None)
            print("  请输入 y/n")

    def injections(self) -> list[InjectionInput]:
        injections: list[InjectionInput] = []
        while not self._injection_queue.empty():
            try:
                injections.append(self._injection_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return injections

    def inject(self, content: str) -> None:
        self._injection_queue.put_nowait(InjectionInput(content=content))
