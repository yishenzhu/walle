"""上游地址校验：BYOK 的 base_url 不能指向内网（防 SSRF）。"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlsplit

PRIVATE_OK = ("1", "true", "yes", "on")


def allow_private() -> bool:
    return os.getenv("WALLE_ALLOW_PRIVATE_UPSTREAM", "0").lower() in PRIVATE_OK


def resolve_host(host: str) -> str:
    """解析主机名：private / public / unresolved（解析失败不等于内网）。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return "unresolved"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return "unresolved"
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return "private"
    return "public"


async def validate_upstream(base_url: str) -> None:
    """校验 base_url；不合法时抛 ValueError（由帧处理器回错误帧）。"""
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("base_url 必须是 http(s) 地址")
    if allow_private():
        return

    state = "unresolved"
    for _ in range(2):  # 容器 DNS 偶发抖动，重试一次
        state = await asyncio.to_thread(resolve_host, parts.hostname)
        if state != "unresolved":
            break
        await asyncio.sleep(0.3)

    if state == "unresolved":
        raise ValueError(
            f"base_url 域名解析失败（{parts.hostname}），请检查容器网络或稍后重试"
        )
    if state == "private":
        raise ValueError(
            f"base_url 指向内网地址（{parts.hostname}），"
            "未启用 WALLE_ALLOW_PRIVATE_UPSTREAM"
        )


def upstream_headers(cfg, session_id: str) -> dict[str, str]:
    """构造上游请求头：替换 {{session_id}}，并为已知端点补会话路由头。"""
    headers = {
        name: str(value).replace("{{session_id}}", session_id)
        for name, value in (cfg.headers or {}).items()
    }
    host = (urlsplit(cfg.base_url).hostname or "").lower()
    has_session = any(name.lower() == "x-opencode-session" for name in headers)
    if host.endswith("opencode.ai") and not has_session:
        headers["x-opencode-session"] = (
            session_id if session_id.startswith("ses_") else f"ses_{session_id}"
        )
    return headers
