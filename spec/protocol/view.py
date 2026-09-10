"""工具执行期可见的会话能力面（能力收窄的视图协议）。

工具与审批扩展只经 tool_context 拿到本面，绝不 import Session /
SessionContext 具体类。刻意不含 provider / agents / depth / session_id
（那些属引擎内部，工具无需知晓）。
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
    jobs: dict[str, Job]
    cwd: str | None
    history: Messages
    ext_runner: ToolTable | None
    bus: EventBus | None
