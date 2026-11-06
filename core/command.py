"""CommandService 默认实现：注册 / 查找 / 执行；handler 返回结构化结果。"""

from __future__ import annotations

import inspect
import re
import uuid

from ..plugin import on_dispose
from ..spec import (
    Command,
    CommandContext,
    CommandDescriptor,
    CommandRequest,
    CommandResult,
)

COMMAND_RE = re.compile(r"^/([a-z][a-z0-9_-]*)(?=$|[\t\n\r ])")


def parse_command(line: str) -> tuple[str, str] | None:
    """解析斜杠命令；返回 (name, raw_input)，非命令返回 None。"""
    match = COMMAND_RE.match(line)
    if match is None:
        return None
    return match.group(1), line[match.end() :].strip()


class Commands:
    """会话级命令目录；同一实例内 name 不允许重复。"""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        """注册命令；名称冲突时报错。"""
        if command.name in self._commands:
            raise ValueError(f"command '{command.name}' already registered")
        self._commands[command.name] = command

        def off() -> None:
            if self._commands.get(command.name) is command:
                del self._commands[command.name]

        on_dispose(off)

    def find(self, name: str) -> Command | None:
        """按名称查找命令。"""
        return self._commands.get(name)

    def list(self) -> list[CommandDescriptor]:
        """全部命令的只读描述。"""
        return [
            CommandDescriptor(
                name=command.name,
                description=command.description,
                input_hint=command.input_hint,
            )
            for command in self._commands.values()
        ]

    async def execute(
        self,
        line: str,
        ctx: CommandContext,
    ) -> CommandResult | None:
        """分发斜杠命令；非命令或未注册返回 None。"""
        parsed = parse_command(line)
        if parsed is None:
            return None
        name, raw_input = parsed
        command = self.find(name)
        if command is None:
            return None
        request = CommandRequest(
            command_id=f"cmd-{uuid.uuid4().hex}",
            raw_input=raw_input,
        )
        try:
            result = command.handler(request, ctx)
            if inspect.isawaitable(result):
                result = await result
            if result is None:
                return CommandResult()
            if isinstance(result, str):
                return CommandResult(text=result)
            return result
        except Exception as exc:  # noqa: BLE001 - 命令失败转为结构化结果
            return CommandResult(kind="error", text=str(exc))
