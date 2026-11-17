from typing import Self

from pydantic import BaseModel, ConfigDict


class Usage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: Self):
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
