from pydantic import BaseModel


class UserInput(BaseModel):
    content: str | None = None  # None = 无输入 / 退出信号（Ctrl+C / EOF）


class ApprovalRsp(BaseModel):
    approved: bool
    reason: str | None = None
