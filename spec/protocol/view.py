"""工具执行期可见的会话能力面（能力收窄的视图协议）。"""

from typing import Any, Protocol

from ..schemas import Job
from .channel import Channel
from .events import EventBus
from .messages import Messages
from .runtime import ToolTable


class SessionView(Protocol):
    """工具执行期可见的会话状态（SessionContext 即实现）。"""

    channel: Channel | None
    session_id: str | None
    jobs: dict[str, Job]
    cwd: str | None
    history: Messages
    tool_table: ToolTable | None
    bus: EventBus | None
    metadata: dict[str, Any]
