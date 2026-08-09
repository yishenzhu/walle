"""通知观察者：消费 notify 流做渲染 / 审计，不参与交互。"""
import logging

from ..schemas import (
    Delta,
    DeltaEnd,
    Error,
    NotificationUnion,
    ToolResult,
    ToolStart,
)

logger = logging.getLogger(__name__)


class LogObserver:
    """审计观察者：通知沉淀到结构化日志，供审计 / 回放。"""

    async def __call__(self, n: NotificationUnion) -> None:
        logger.info("ui_event", extra={"event": n.model_dump(mode="json")})


class ConsoleObserver:
    """纯观察者：把 notify 渲染到终端（不参与交互，供无头模式本地调试）。"""

    async def __call__(self, n: NotificationUnion) -> None:
        match n:
            case Delta(delta=delta):
                print(delta, end="", flush=True)
            case DeltaEnd():
                print()
            case ToolStart(tool_name=name, arguments=args):
                print(f"  [调用工具 {name}({args})]", flush=True)
            case ToolResult(tool_call_id=tc_id, result=result, error=None):
                print(f"  [工具结果 {tc_id}] {str(result)[:512]}", flush=True)
            case ToolResult(tool_call_id=tc_id, error=err):
                print(f"  [工具错误 {tc_id}] {err}", flush=True)
            case Error(message=msg):
                print(f"  [错误] {msg}", flush=True)
