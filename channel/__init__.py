"""交互通道：通道能力协议定义在协议包（protocol.channel），实现在 channel/。"""

from ..spec import Channel, SessionConn, Sessions

__all__ = [
    "Channel",
    "SessionConn",
    "Sessions",
]