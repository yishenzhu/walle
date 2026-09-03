"""工具容器：扩展系统注册工具的唯一落点，executor/agent 的工具查询源。

架构：所有工具（内置 bash/ask_user/read 等、MCP 远端工具、用户扩展工具）
一律经扩展系统（ExtensionRegistry → 声明 → 会话 ExtensionRunner.activate →
add_tool）进入本容器。本类不认识任何具体工具，也不持有 MCP——MCP 客户端
由 main 进程级持有并组装成扩展声明，远端工具随扩展进各会话工具表。
"""

import logging

from .tool import Tool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """工具表：同名后注册者覆盖先注册者，remove_tool 摘除，all_tools 查询。"""

    def __init__(self):
        self._tools: list[Tool] = []

    def add_tool(self, *tools: Tool) -> None:
        """注册工具：同名时新工具顶替旧工具（后到者胜）。

        同一批内工具名重复视为编程错误（抛 ValueError）；与已注册工具重名
        则整批替换（后到者胜：扩展可覆盖内置/其它扩展）。
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

    def all_tools(self) -> list[Tool]:
        """全部工具。"""
        return list(self._tools)
