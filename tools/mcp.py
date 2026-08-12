from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from contextlib import AsyncExitStack

import httpx
import yaml
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from ..conf import MCPConfig
from .tool import Tool

logger = logging.getLogger(__name__)

_MCP_FILENAME = "mcp.yaml"


class MCP:
    """MCP server 配置持久化（.agent/mcp.yaml）+ 客户端管理。"""

    def __init__(self, root: Path | None = None):
        if root is None:
            from ..conf import DOT_AGENT

            root = DOT_AGENT
        self._root = root
        self._clients: list[MCPClient] = []

    @property
    def path(self) -> Path:
        return self._root / _MCP_FILENAME

    @property
    def clients(self) -> list[MCPClient]:
        """已连接的客户端列表。"""
        return self._clients

    def save(self, name: str, conf: MCPConfig) -> Path:
        configs = self.load_all()
        configs[name] = conf

        self._root.mkdir(parents=True, exist_ok=True)
        p = self.path
        p.write_text(
            yaml.safe_dump(
                {k: v.model_dump(exclude_none=True) for k, v in configs.items()},
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return p

    def load_all(self) -> dict[str, MCPConfig]:
        if not self.path.exists():
            return {}
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            return {k: MCPConfig.model_validate(v) for k, v in data.items()}
        except Exception as e:
            logger.warning(f"mcp config load failed ({self.path}): {e}")
            return {}

    def has(self, name: str) -> bool:
        return any(c.name == name for c in self._clients)

    async def add(self, name: str, conf: MCPConfig) -> MCPClient | None:
        """添加并连接 server：查重 → 连接 → 成功才持久化，失败返回 None。"""
        if self.has(name):
            raise ValueError(f"MCP server 已存在: {name}")

        client = await MCPClient(name, conf).connect()
        if client is None:
            return None
        await client.fetch_tools()

        self.save(name, conf)
        self._clients.append(client)
        return client

    async def connect(self) -> list[MCPClient]:
        """连接全部启用的 server，返回成功的客户端列表。"""
        clients = await asyncio.gather(
            *[
                MCPClient(name, c).connect()
                for name, c in self.load_all().items()
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

    def _prefixed(self, tool_name: str):
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
                    name=self._prefixed(tool.name),
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

    async def aclose(self):  # 兼容 contextlib.aclosing 协议
        await self.close()
