"""CLI 通道：服务端（server.CLIServer）+ 客户端（client.CLIClient）。

入口：python -m walle.channel.cli（__main__.py）。
"""

from .client import CLIClient
from .server import HOST, PORT, CLIConnection, CLIServer

__all__ = ["CLIServer", "CLIClient", "CLIConnection", "HOST", "PORT"]
