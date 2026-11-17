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


def private_host(host: str) -> bool:
    """解析主机名并判断是否指向内网/回环/链路本地地址。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return True
    return False


async def validate_upstream(base_url: str) -> None:
    """校验 base_url；不合法时抛 ValueError（由帧处理器回错误帧）。"""
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("base_url 必须是 http(s) 地址")
    if allow_private():
        return
    if await asyncio.to_thread(private_host, parts.hostname):
        raise ValueError(
            f"base_url 指向内网地址（{parts.hostname}），"
            "未启用 WALLE_ALLOW_PRIVATE_UPSTREAM"
        )
