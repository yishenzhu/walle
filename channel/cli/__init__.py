"""CLI 通道：服务端（server.CLIChannel）+ 客户端（client.CLIClient）。

入口：python -m walle.channel.cli（__main__.py）。
"""

from .client import CLIClient
from .server import HOST, PORT, CLIChannel, CLIConn

__all__ = ["CLIChannel", "CLIClient", "CLIConn", "HOST", "PORT"]
