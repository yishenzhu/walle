from pydantic import BaseModel


class UserInput(BaseModel):
    content: str


class ApprovalRsp(BaseModel):
    approved: bool
    reason: str | None = None
