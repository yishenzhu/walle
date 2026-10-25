"""Agent 服务能力面：当前 agent 与按名解析。"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AgentService(Protocol):
    """会话当前 agent 与 agent 注册表。"""

    @property
    def current(self) -> Any: ...

    def get(self, name: str) -> Any: ...
