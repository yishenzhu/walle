from pydantic import BaseModel
from typing import Any


class TextDelta(BaseModel):
    delta: str


class TextDeltaEnd(BaseModel):
    pass


class ToolCall(BaseModel):
    tool_name: str
    arguments: dict
    tool_call_id: str | None = None


class ToolResult(BaseModel):
    tool_call_id: str
    result: Any


class UserInput(BaseModel):
    content: str


class InjectionInput(BaseModel):
    content: str


class ApprovalResponse(BaseModel):
    approved: bool
    reason: str | None = None
