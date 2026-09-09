"""CLI 通道入口：python -m walle.channel.cli 启动交互客户端。

实现拆分：服务端在 .server（CLIChannel），客户端交互在 .client（CLIClient）。
"""

import asyncio

from .client import CLIClient


def main() -> None:
    """CLI 入口：python -m walle.channel.cli [--attach <id>] [--extensions a,b] [--list]。"""
    import argparse

    parser = argparse.ArgumentParser(prog="walle-cli", description="walle CLI 客户端")
    parser.add_argument("--attach", default="", help="恢复已有会话（attach）")
    parser.add_argument(
        "--extensions",
        default="",
        help="新会话激活的扩展名（逗号分隔，缺省全部）",
    )
    parser.add_argument(
        "--dir",
        default="",
        help="会话工作目录（缺省 = 客户端启动目录；bash 执行与沙箱可写区基准）",
    )
    parser.add_argument("--list", action="store_true", help="浏览会话（仅元数据）")
    parser.add_argument("--api-key", default="", help="本会话模型 API Key（缺省用服务端配置）")
    parser.add_argument("--base-url", default="", help="本会话模型端点（OpenAI 兼容）")
    parser.add_argument("--model", default="", help="本会话模型名")
    args = parser.parse_args()

    if args.list:
        asyncio.run(CLIClient.list_sessions())
    else:
        exts = [e.strip() for e in args.extensions.split(",") if e.strip()] or None
        # 三项齐全才发模型配置（缺省由服务端用进程默认 provider）
        model = (
            {"api_key": args.api_key, "base_url": args.base_url, "model": args.model}
            if args.api_key and args.base_url and args.model
            else None
        )
        asyncio.run(
            CLIClient(
                attach=args.attach, extensions=exts, dir=args.dir or None, model=model
            ).run()
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()  # Ctrl+C 优雅退出，不打印栈
