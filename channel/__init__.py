"""交互通道：协议在 spec，实现在 channel/。

当前提供 Web 通道；server.py 是通道无关的会话服务端。"""

from ..spec import Channel, SessionInit, Sessions, SessionSpec
from .server import SessionConn, SessionServer

__all__ = [
    "Channel",
    "SessionConn",
    "SessionInit",
    "SessionServer",
    "SessionSpec",
    "Sessions",
]