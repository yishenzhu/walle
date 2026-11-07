"""会话管理器 + 工厂：session_id → Session。"""

from __future__ import annotations

import time
from typing import Any

from ..conf import TimeoutConfig
from ..infra import OpenAIProvider
from ..messages import build_history, list_sessions, list_workspaces
from ..plugin import Plugin
from ..spec import LLM, SessionInit
from .session import Session, SessionRuntime


class SessionManager:
    """进程级会话管理器 + 工厂：session_id → Session。"""

    def __init__(
        self,
        timeout: TimeoutConfig | None = None,
        plugins: list[Plugin] | None = None,
        plugin_configs: dict[str, dict[str, Any]] | None = None,
        provider: LLM | None = None,
        storage: str = "file",
        path: str | None = None,
    ) -> None:
        self._timeout = timeout
        self._plugins: list[Plugin] = plugins or []
        self._plugin_configs = plugin_configs or {}
        self._provider = provider
        self._storage = storage
        self._path = path
        self._sessions: dict[str, Session] = {}

    def create(
        self,
        conn: SessionInit,
        plugin_names: list[str] | None = None,
    ) -> Session:
        """新建会话并注册。"""
        plugins = self._select_plugins(plugin_names)
        session = Session(
            session_id=conn.session_id,
            history=build_history(
                self._storage,
                self._path,
                conn.session_id,
                cwd=conn.cwd,
            ),
            runtime=SessionRuntime.build(),
            timeout=self._timeout,
            plugins=plugins,
            plugin_configs=self._plugin_configs,
            provider=self._provider_for(conn),
            cwd=conn.cwd,  # 客户端工作目录（连接握手携带）
            agent=conn.agent,  # 客户端指定 agent（None = default）
        )
        session.attach(conn)
        self.register(session)
        return session

    def _provider_for(self, conn: SessionInit) -> LLM | None:
        """客户端带模型配置时新建会话级 provider，否则沿用进程默认。"""
        cfg = conn.model
        if cfg is None:
            return self._provider
        return OpenAIProvider(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model,
        )

    def _select_plugins(self, plugin_names: list[str] | None) -> list[Plugin]:
        """从插件声明池按名单挑选（默认全部）。"""
        if plugin_names is None:
            return list(self._plugins)
        wanted = set(plugin_names)
        return [plugin for plugin in self._plugins if plugin.name in wanted]

    def register(self, session: Session) -> None:
        """注册新会话。同 id 已存在则报错（重连走 attach，不重建）。"""
        if session.id in self._sessions:
            raise ValueError(f"session '{session.id}' already registered")
        self._sessions[session.id] = session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> Session | None:
        """从管理器移除（不 close，调用方决定是否销毁）。"""
        return self._sessions.pop(session_id, None)

    def list(self) -> list[dict]:
        """列出全部会话：id、attached 状态、创建时间。"""
        return [
            {
                "session_id": sid,
                "attached": s.attached,
                "created_at": s.created_at,
                "age_seconds": time.time() - s.created_at,
            }
            for sid, s in self._sessions.items()
        ]

    def list_workspaces(self) -> list[dict]:
        """已登记的工作目录（file 后端）；其他后端暂不提供历史目录。"""
        if self._storage != "file":
            return []
        return list_workspaces(self._path)

    def list_sessions(self, cwd: str) -> list[dict]:
        """某个工作目录下的历史会话（file 后端），按最近更新倒序。"""
        if self._storage != "file" or not cwd:
            return []
        return list_sessions(cwd, self._path)

    async def close(self) -> None:
        """销毁全部会话（服务端停机）。"""
        for s in self._sessions.values():
            try:
                await s.close()
            except Exception:
                pass
        self._sessions.clear()
