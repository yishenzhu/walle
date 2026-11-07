"""AgentService 视图：当前 agent 与按名加载。"""

from __future__ import annotations

from .agent import Agent
from .session import Session


class Agents:
    """AgentService：当前 agent 与按名加载（会话视图）。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def current(self) -> Agent:
        return self._session.agent

    def get(self, name: str) -> Agent:
        return self._session._build_agent(name)
