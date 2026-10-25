"""通道与交互协议：服务端/传输层与上层会话的唯一稳定契约。

从 notify（广播）/ call（点对点）两个原语，到会话身份与管理能力，
本模块只声明能力面，不涉及任何具体实现。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..schemas import ModelConfig, NotificationUnion, RequestUnion


@runtime_checkable
class Channel(Protocol):
    async def notify(self, notification: NotificationUnion) -> None: ...  # 广播，无返回
    async def call(self, request: RequestUnion) -> Any: ...               # 点对点，有返回


class SessionInit(Protocol):
    """创建会话所需的最小输入面；连接端与规格对象都可满足。"""

    session_id: str
    cwd: str | None
    model: ModelConfig | None
    agent: str | None


@runtime_checkable
class Sessions(Protocol):
    """通道可见的会话能力：get / create / list / 浏览 / close。

    使用方只依赖本能力面，不 import 具体实现；register/remove 属会话
    装配根内部管理，不暴露给 channel。
    """

    def get(self, session_id: str) -> Any: ...
    def create(
        self,
        conn: SessionInit,
        extension_names: list[str] | None = None,
    ) -> Any: ...
    def list(self) -> list[dict]: ...
    def list_workspaces(self) -> list[dict]: ...
    def list_sessions(self, cwd: str) -> list[dict]: ...
    async def close(self) -> None: ...