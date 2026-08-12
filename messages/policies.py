from pydantic import BaseModel
from typing import Protocol, runtime_checkable
from ..schemas import Message, Usage

PROMPT_LIMIT = 2048


class CompressionContext(BaseModel):
    items: list[Message]
    last_usage: Usage | None = None


@runtime_checkable
class CompressionPolicy(Protocol):
    def hit(self, ctx: CompressionContext) -> bool: ...


class PromptLimitPolicy:
    def __init__(self, limit: int = PROMPT_LIMIT):
        if limit < 0:
            raise ValueError("limit must be >= 0")
        self.limit = limit

    def hit(self, ctx: CompressionContext) -> bool:
        return bool(ctx.last_usage and ctx.last_usage.prompt_tokens > self.limit)
