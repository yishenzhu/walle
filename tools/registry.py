import asyncio
import contextlib
import logging
from typing import Self

from ..conf import Config, MCPConfig, VaultConfig
from ..vault import make_search_notes
from .tool import Tool
from .mcp import MCPClient
from .builtin import Skill, ask_user, bash

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self):
        self._builtin: list[Tool] = []
        self.add_tool(*Skill.load())
        self._mcp_clients: list[MCPClient] = []
        self._stack = contextlib.AsyncExitStack()  # 资源生命周期：逆序自动关闭
        # define_tool 是元工具（操作 registry 自身），作为实例方法注册
        self.add_function(ask_user, bash, self.define_tool)

    async def define_tool(self, name: str, code: str) -> str:
        """用代码定义一个可复用工具，立即生效并持久化。

        code 中需定义顶层 async def <name>() 并写 docstring（docstring 即工具描述）。
        """
        from ..conf import DOT_AGENT
        from .dynamic import DynamicToolStore, ToolCodeError, load_from_file, validate_code

        store = DynamicToolStore(DOT_AGENT / "tools")
        try:
            validate_code(code, name)
        except ToolCodeError as e:
            return f"定义失败: {e}"

        try:
            path = store.save(name, code)
            fn = load_from_file(str(path), name)
        except (ToolCodeError, OSError) as e:
            return f"定义失败: {e}"

        try:
            self.add_function(fn)
        except ValueError as e:
            return f"定义失败: {e}"
        return f"工具已定义并生效: {name}"

    def add_function(self, *fns) -> None:
        """把任意函数注册为工具（描述取自函数 docstring）。"""
        for fn in fns:
            tool = Tool.from_function(fn)
            self.add_tool(tool)

    def add_tool(self, *tools: Tool) -> None:
        """注册一个或多个已构造好的工具（先统一查重，保证原子性）。"""
        existing = {t.name for t in self._builtin}
        for tool in tools:
            if tool.name in existing:
                raise ValueError(f"Duplicate tool name: {tool.name}")
        for tool in tools:
            self._builtin.append(tool)
            logger.info(f"tool registered: {tool.name}")

    async def initialize(self, conf: Config) -> Self:
        """初始化工具系统：知识库 + MCP server + 动态工具。"""
        await self.setup_vault(conf.vault)
        await self.load_mcp(conf.mcp)
        self.load_dynamic()
        return self

    async def setup_vault(self, conf: VaultConfig | None) -> None:
        """装配知识库：建索引并注册笔记检索工具（生命周期由 exit stack 管理）。"""
        if conf is None or not conf.enabled or not conf.path:
            return

        fn = await self._stack.enter_async_context(make_search_notes(conf))
        self.add_function(fn)

    def load_dynamic(self) -> None:
        """启动时恢复已持久化的动态代码工具。"""
        from ..conf import DOT_AGENT
        from .dynamic import DynamicToolStore, load_from_file, validate_code

        store = DynamicToolStore(DOT_AGENT / "tools")
        for name, code in store.load_all().items():
            try:
                validate_code(code, name)
                path = store.dir_for(name) / "code.py"
                fn = load_from_file(str(path), name)
                self.add_function(fn)
                logger.info(f"dynamic tool restored: {name}")
            except Exception as e:
                logger.warning(f"dynamic tool load failed ({name}): {e}")

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
        for c in clients:
            await self._stack.enter_async_context(contextlib.aclosing(c))
        return self

    def all_tools(self) -> list[Tool]:
        """全部工具（builtin + MCP）。工具筛选由模型自己完成。"""
        tools = list(self._builtin)
        for c in self._mcp_clients:
            tools.extend(c.tools)
        return tools

    async def close(self) -> None:
        await self._stack.aclose()
