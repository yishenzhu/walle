"""工具执行期可见的会话能力面（能力收窄的视图协议）。

工具与审批扩展只经 tool_context 拿到本面，绝不 import Session /
SessionContext 具体类。刻意不含 provider / agents / depth（属引擎内部）；
session_id 仅用于按会话归属落盘的工具（如 canvas），不暴露会话内容。
"""

from typing import Protocol

from ..schemas import Job
from .channel import Channel
from .events import EventBus
from .messages import Messages
from .runtime import ToolTable


class SessionView(Protocol):
    """工具执行期可见的会话状态（SessionContext 即实现）。

    仅用于类型标注，故不加 runtime_checkable。
    """

    channel: Channel | None
    session_id: str | None
    jobs: dict[str, Job]
    cwd: str | None
    history: Messages
    extension_runner: ToolTable | None
    bus: EventBus | None
