"""交互通道：通道能力协议定义在协议包（protocol.channel），实现在 channel/。"""

from ..spec import Channel, Sessions, SessionSpec

__all__ = [
    "Channel",
    "SessionSpec",
    "Sessions",
]