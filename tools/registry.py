import asyncio
import logging
from typing import Self

from ..conf import Config, MCPConfig, VaultConfig
from .tool import Tool
from .mcp import MCPClient
from .builtin import Skill, ask_user, bash, make_semantic_search

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self):
        self._builtin: list[Tool] = Skill.load()
        self.add_builtin(ask_user, bash)
        self._mcp_clients: list[MCPClient] = []
        self._store = None

    def add_builtin(self, *fns) -> None:
        for fn in fns:
            tool = Tool.from_function(fn)
            if any(t.name == tool.name for t in self._builtin):
                raise ValueError(f"Duplicate tool name: {tool.name}")
            self._builtin.append(tool)

    async def initialize(self, conf: Config) -> Self:
        """初始化工具系统：知识库 + MCP server。"""
        await self.setup_vault(conf.vault)
        await self.load_mcp(conf.mcp)
        return self

    async def setup_vault(self, conf: VaultConfig | None) -> None:
        """装配知识库：建索引并注册笔记检索工具（闭包捕获）。"""
        if conf is None or not conf.enabled or not conf.path:
            return
        from ..vault import Store, Indexer, Retriever

        store = Store()
        indexer = Indexer(conf.path, store)
        await indexer.ensure_indexed()
        self.add_builtin(make_semantic_search(Retriever(store, indexer)))
        self._store = store

    async def load_mcp(self, configs: dict[str, MCPConfig]) -> Self:
        clients = await asyncio.gather(
            *[
                MCPClient(name, conf).connect()
                for name, conf in configs.items()
                if conf.enabled
            ]
        )
        clients = [c for c in clients if c is not None]
        await asyncio.gather(*[c.fetch_tools() for c in clients])
        self._mcp_clients = clients
        return self

    def builtin_tools(
        self,
        include: set[str] | None = None,
        exclude: set[str] | None = None,
    ) -> list[Tool]:
        names = {t.name for t in self._builtin}
        if include is not None:
            names &= include
        if exclude is not None:
            names -= exclude
        return [t for t in self._builtin if t.name in names]

    def mcp_tools(
        self,
        include_servers: set[str] | None = None,
        exclude_servers: set[str] | None = None,
    ) -> list[Tool]:
        server_names = {c.name for c in self._mcp_clients}
        if include_servers is not None:
            server_names &= include_servers
        if exclude_servers is not None:
            server_names -= exclude_servers

        tools: list[Tool] = []
        for c in self._mcp_clients:
            if c.name in server_names:
                tools.extend(c.tools)
        return tools

    async def close(self) -> None:
        for client in self._mcp_clients:
            await client.close()
        if self._store is not None:
            await self._store.close()
