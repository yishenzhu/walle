from pydantic import BaseModel, Field, TypeAdapter
from typing import Any, Literal, Annotated, Self


class Message(BaseModel):
    role: Literal["user", "assistant", "system", "tool"]


class UserMessage(Message):
    role: Literal["user"] = "user"
    content: str


class SystemMessage(Message):
    role: Literal["system"] = "system"
    content: str


class AssistantMessage(Message):
    role: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None

    @classmethod
    def from_response(cls, message: Any) -> Self:
        tool_calls = None
        if message.tool_calls:
            tool_calls = [tc.model_dump() for tc in message.tool_calls]
        return cls(content=message.content, tool_calls=tool_calls)


class ToolMessage(Message):
    role: Literal["tool"] = "tool"
    content: str
    tool_call_id: str


AnyMessage = Annotated[
    UserMessage | SystemMessage | AssistantMessage | ToolMessage,
    Field(discriminator="role"),
]
MessageAdapter = TypeAdapter(AnyMessage)
