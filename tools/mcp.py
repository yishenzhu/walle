from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx
import yaml
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from ..conf import AGENT_DIR, MCPConfig
from ..infra import Tool
from ..plugin import Plugin
from ..spec import ToolService

logger = logging.getLogger(__name__)

# MCP server 连接/握手超时（秒）：server 不可达时快速失败，
# 避免拖住 agent 启动。stdio 与 HTTP 型统一。
CONNECT_TIMEOUT = 15


class MCPManager:
    """MCP server 配置持久化（.agent/mcp.yaml）+ 客户端注册管理。"""

    def __init__(self, path: Path | None = None):
        self._path = path or AGENT_DIR / "mcp.yaml"
        self._clients: list[MCPClient] = []

    def save(self, name: str, conf: MCPConfig) -> Path:
        configs = self.load()
        configs[name] = conf

        self._path.parent.mkdir(parents=True, exist_ok=True)
        p = self._path
        p.write_text(
            yaml.safe_dump(
                {k: v.model_dump(exclude_none=True) for k, v in configs.items()},
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return p

    def load(self) -> dict[str, MCPConfig]:
        if not self._path.exists():
            return {}
        try:
            data = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
            return {k: MCPConfig.model_validate(v) for k, v in data.items()}
        except Exception as e:
            logger.warning(f"mcp config load failed ({self._path}): {e}")
            return {}

    async def connect(self) -> list[MCPClient]:
        """连接全部启用的 server，返回成功的客户端列表。"""
        clients = await asyncio.gather(
            *[
                MCPClient(name, c).connect()
                for name, c in self.load().items()
                if c.enabled
            ]
        )
        clients = [c for c in clients if c is not None]
        await asyncio.gather(*[c.fetch_tools() for c in clients])
        self._clients = clients
        return clients

    async def close(self) -> None:
        """关闭全部客户端。"""
        for c in self._clients:
            await c.close()

    def plugin(self) -> Plugin:
        """返回 MCP 插件声明：把已连接客户端的远端工具注册进会话。"""
        return Plugin(
            name="mcp",
            apply=self._apply,
            inject=(ToolService,),
        )

    async def _apply(self, ctx, config) -> None:
        """把全部已连接客户端的远端工具注册进 ToolService。"""
        tools = ctx.get(ToolService)
        for client in self._clients:
            for tool in client.tools:
                tools.add(tool)


class MCPClient:
    def __init__(self, name: str, conf: MCPConfig):
        self._name = name
        self._conf = conf
        self._exit_stack = AsyncExitStack()
        self._tools: list[Tool] = []

    @property
    def name(self) -> str:
        return self._name

    async def connect(self):
        try:
            params = self._conf.model_dump(exclude={"enabled"}, exclude_none=True)

            if self._conf.command:
                (
                    read_stream,
                    write_stream,
                ) = await self._exit_stack.enter_async_context(
                    stdio_client(StdioServerParameters(**params))
                )
                self._session = await self._exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
            elif self._conf.url:
                del params["url"]
                # timeout 语义为秒（httpx 原生），未配置用默认
                (
                    read_stream,
                    write_stream,
                    get_session_id,
                ) = await self._exit_stack.enter_async_context(
                    streamable_http_client(
                        self._conf.url,
                        http_client=httpx.AsyncClient(**params) if params else None,
                    )
                )
                self._session = await self._exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                self._session_id = get_session_id()

            # 握手加显式超时：server 不可达时（如 HTTP 半开、子进程起不来）
            # 快速失败，避免拖住 agent 启动。stdio 与 HTTP 型统一。
            timeout_s = self._conf.timeout or CONNECT_TIMEOUT
            async with asyncio.timeout(timeout_s):
                await self._session.initialize()
            logger.info(f"mcp connected: {self._name}")
            return self
        except (Exception, asyncio.CancelledError) as e:
            logger.error(f"mcp connect failed: {self._name}: {e}")
            # 清理已进入的 context（anyio cancel scope 必须在同一 task 内退出）
            try:
                await self._exit_stack.aclose()
            except Exception:
                pass
            return None

    def _prefixed_tool_name(self, tool_name: str):
        return f"mcp_{self._name}_{tool_name}"

    async def fetch_tools(self):
        if self._session is None:
            raise RuntimeError("client is not connected")

        result = await self._session.list_tools()
        logger.debug(f"mcp {self._name}: {len(result.tools)} tools loaded")
        tools: list[Tool] = []

        for tool in result.tools:

            def make_fn(tool_name: str):
                async def fn(args: dict[str, Any]) -> str:
                    result = await self._session.call_tool(tool_name, args)
                    text = "\n".join(
                        block.text
                        for block in result.content
                        if isinstance(block, TextContent)
                    )
                    if result.isError:
                        raise RuntimeError(text)
                    return text

                return fn

            tools.append(
                Tool(
                    name=self._prefixed_tool_name(tool.name),
                    description=tool.description or "",
                    parameters=tool.inputSchema,
                    fn=make_fn(tool.name),
                )
            )

        self._tools = tools

    @property
    def tools(self) -> list[Tool]:
        return self._tools

    async def close(self):
        try:
            await self._exit_stack.aclose()
        except (asyncio.CancelledError, RuntimeError):
            pass
