"""启动 Web 对话：python -m walle.channel.web。"""

import argparse
import asyncio

import uvicorn

from .app import app, install_web_plugins


def main() -> None:
    parser = argparse.ArgumentParser(prog="walle-web", description="walle Web 对话")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    asyncio.run(install_web_plugins())  # 路由须在 serve 前挂好
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
