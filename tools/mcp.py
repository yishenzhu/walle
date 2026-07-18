import asyncio
from typing import Any
from contextlib import AsyncExitStack
import httpx
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent
import logging
from ..conf import MCPConfig
from .tool import Tool

logger = logging.getLogger(__name__)


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
