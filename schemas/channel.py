from pydantic import BaseModel


class UserInput(BaseModel):
    content: str


class ApprovalResponse(BaseModel):
    approved: bool
    reason: str | None = None
