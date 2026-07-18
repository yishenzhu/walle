from pydantic import BaseModel, ConfigDict
from typing import Self


class Usage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def add(self, other: Self):
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
