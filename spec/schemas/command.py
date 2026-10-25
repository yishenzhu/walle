"""命令请求与结果：只含本次调用数据，宿主能力由 CommandContext 提供。"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CommandRequest:
    """一次命令调用的数据：id 与原始输入。"""

    command_id: str
    raw_input: str


@dataclass(frozen=True)
class CommandResult:
    """命令的结构化结果；text 为空表示静默。"""

    kind: Literal["success", "error"] = "success"
    text: str | None = None


@dataclass(frozen=True)
class CommandDescriptor:
    """命令的只读发现信息（不含 handler）。"""

    name: str
    description: str
    aliases: tuple[str, ...] = ()
    input_hint: str | None = None
