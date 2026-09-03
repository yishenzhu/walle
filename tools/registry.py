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
from .mcp import MCPRegistry

logger = logging.getLogger(__name__)


class ToolRegistry:
    """工具表：同名后注册者覆盖先注册者，remove_tool 摘除，all_tools 查询。

    mcp 为进程级共享的 MCP 客户端容器（main 连接一次，各会话工具表共享
    其远端工具视图）；缺省自建（测试用，不自动连接）。
    """

    def __init__(self, mcp: MCPRegistry | None = None):
        self._tools: list[Tool] = []
        self._mcp = mcp or MCPRegistry()

    def add_tool(self, *tools: Tool) -> None:
        """注册工具：同名时新工具顶替旧工具（后到者胜）。

        同一批内工具名重复视为编程错误（抛 ValueError）；与已注册工具重名
        则整批替换（pi 后到者胜语义：扩展可覆盖内置/其它扩展/MCP 工具）。
        """
        names = [t.name for t in tools]
        if len(set(names)) != len(names):
            raise ValueError(f"Duplicate tool name in batch: {names}")
        new_names = set(names)
        self._tools = [t for t in self._tools if t.name not in new_names]
        for tool in tools:
            self._tools.append(tool)
            logger.info(f"tool registered: {tool.name}")

    def remove_tool(self, name: str) -> None:
        """按名摘除一个工具（扩展卸载时用）。不存在则忽略。"""
        before = len(self._tools)
        self._tools = [t for t in self._tools if t.name != name]
        if len(self._tools) < before:
            logger.info(f"tool removed: {name}")
        else:
            logger.warning(f"tool not found: {name}")

    async def initialize(self, conf: Config) -> Self:
        """加载 MCP server（读 .agent/mcp.yaml 配置并连接）。"""
        await self.load_mcp()
        return self

    async def load_mcp(self) -> Self:
        """加载 MCP server：读取 .agent/mcp.yaml 配置并连接。"""
        await self._mcp.connect()
        return self

    def all_tools(self) -> list[Tool]:
        """全部工具（本地 + 未被本地占名的 MCP 远端工具）。"""
        tools = list(self._tools)
        taken = {t.name for t in tools}
        for c in self._mcp.clients:
            for t in c.tools:
                if t.name not in taken:  # 本地已占名（含扩展覆盖）→ MCP 让位
                    tools.append(t)
        return tools

    async def close(self) -> None:
        await self._mcp.close()  # 关闭全部 MCP 客户端
