"""命令服务能力面：注册 / 查找 / 执行；handler 收 (request, ctx)。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..schemas.command import CommandDescriptor, CommandRequest, CommandResult


class CommandContext(Protocol):
    """命令 handler 可见的宿主能力面：按服务类型取实现。"""

    def get(self, service: type) -> Any: ...


CommandHandler = Callable[
    [CommandRequest, CommandContext], Awaitable[CommandResult] | CommandResult
]


@dataclass(frozen=True)
class Command:
    """命令声明：元数据 + handler；handler 不接收宿主字段。"""

    name: str
    description: str
    handler: CommandHandler
    aliases: tuple[str, ...] = ()
    input_hint: str | None = None


@runtime_checkable
class CommandService(Protocol):
    """会话命令目录：注册 / 查找 / 列表 / 分发。"""

    def register(self, command: Command) -> Callable[[], None]: ...

    def find(self, name: str) -> Command | None: ...

    def list(self) -> list[CommandDescriptor]: ...

    async def execute(
        self,
        line: str,
        ctx: CommandContext,
    ) -> CommandResult | None:
        """分发斜杠命令；非命令或未注册返回 None。"""
