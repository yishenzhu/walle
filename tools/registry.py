"""工具容器：扩展系统注册工具的唯一落点，executor/agent 的工具查询源。

架构：所有工具（内置 bash / ask_user / skills / MCP 等）一律经扩展系统
（ExtensionRegistry → register_tool → activate 提交）进入本容器，本类不
再认识任何具体工具。内置工具 = 随代码注册的引导扩展，用户扩展 = 
.agent/extensions/ 目录发现，两者走同一条注册路径。
"""

import logging
from typing import Self

from ..conf import Config
from .tool import Tool
from .mcp import MCP

logger = logging.getLogger(__name__)


class ToolRegistry:
    """扁平工具表：add_tool 注册（整批查重原子），all_tools 查询，close 收尾。"""

    def __init__(self):
        self._tools: list[Tool] = []
        self._mcp = MCP()

    def add_tool(self, *tools: Tool) -> None:
        """注册一个或多个工具（先统一查重，保证原子性）。"""
        existing = {t.name for t in self._tools}
        for tool in tools:
            if tool.name in existing:
                raise ValueError(f"Duplicate tool name: {tool.name}")
        for tool in tools:
            self._tools.append(tool)
            logger.info(f"tool registered: {tool.name}")

    async def initialize(self, conf: Config) -> Self:
        """加载 MCP server（读 .agent/mcp.yaml 配置并连接）。"""
        await self.load_mcp()
        return self

    async def load_mcp(self) -> Self:
        """加载 MCP server：读取 .agent/mcp.yaml 配置并连接。"""
        await self._mcp.connect()
        return self

    def all_tools(self) -> list[Tool]:
        """全部工具（扩展注册的本地工具 + MCP 远端工具）。"""
        tools = list(self._tools)
        for c in self._mcp.clients:
            tools.extend(c.tools)
        return tools

    async def close(self) -> None:
        await self._mcp.close()  # 关闭全部 MCP 客户端
