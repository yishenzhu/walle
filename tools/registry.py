import asyncio
import logging
from typing import Self

from ..conf import Config, MCPConfig
from .tool import Tool
from .mcp import MCP
from .defined import DefinedTool, ToolCodeError
from .builtin import Skill, ask_user, bash, jupyter, background, job_result

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self):
        self._builtin: list[Tool] = []
        self._mcp = MCP()
        self._defined = DefinedTool()

    async def define_tool(self, name: str, code: str) -> str:
        """用代码定义一个可复用工具，立即生效并持久化。

        code 中需定义顶层 async def <name>() 并写 docstring（docstring 即工具描述）。
        """
        try:
            tool = self._defined.create(name, code)
            self.add_tool(tool)
        except (ToolCodeError, OSError, ValueError) as e:
            return f"定义失败: {e}"
        return f"工具已定义并生效: {name}"

    async def add_mcp(self, name: str, conf: MCPConfig) -> str:
        """添加一个 MCP server，立即连接生效并持久化（重启自动恢复）。

        conf 为完整 MCP 配置：url（HTTP 流式）或 command+args（stdio 子进程）二选一。
        先查重，再连接，连接成功才写入文件。
        """
        try:
            client = await self._mcp.add(name, conf)
        except ValueError as e:
            return f"添加失败: {e}"
        if client is None:
            return "添加失败: 连接失败"

        logger.info(f"mcp server added: {name} ({len(client.tools)} tools)")
        return f"MCP server 已添加并生效: {name}，共 {len(client.tools)} 个工具"

    def add_function(self, *fns) -> None:
        """把任意函数注册为工具（描述取自函数 docstring）。"""
        for fn in fns:
            self.add_tool(Tool.from_function(fn))

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
        """初始化工具系统：注册内置工具 + MCP server + 动态工具。"""
        self.add_tool(*Skill.load())
        # 内置工具 + 后台作业对（background 派发 / job_result 查询）
        # + 元工具（define_tool / add_mcp 操作 registry 自身）
        self.add_function(background, job_result, ask_user, bash, jupyter,
                          self.define_tool, self.add_mcp)
        await self.load_mcp()
        self.load_defined()
        return self

    def load_defined(self) -> None:
        """启动时恢复已持久化的模型定义工具。"""
        tools = self._defined.load()
        if tools:
            self.add_tool(*tools)
            logger.info(f"{len(tools)} defined tools restored")

    async def load_mcp(self) -> Self:
        """加载 MCP server：读取 .agent/mcp.yaml 配置并连接。"""
        await self._mcp.connect()
        return self

    def all_tools(self) -> list[Tool]:
        """全部工具（builtin + MCP）。工具筛选由模型自己完成。"""
        tools = list(self._builtin)
        for c in self._mcp.clients:
            tools.extend(c.tools)
        return tools

    async def close(self) -> None:
        await self._mcp.close()     # 关闭全部 MCP 客户端
