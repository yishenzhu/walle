"""工具钩子的表态类型。"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HookVerdict:
    """工具执行前钩子（PreExecuteToolEvent）的表态：阻止或改写参数。"""

    block: str | None = None
    arguments: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.block is None and self.arguments is None:
            raise ValueError("HookVerdict 必须表态：block=reason 或 arguments=args")

    @property
    def blocks(self) -> bool:
        return self.block is not None
