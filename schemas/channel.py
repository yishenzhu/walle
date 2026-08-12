from pydantic import BaseModel


class UserInput(BaseModel):
    content: str | None = None  # None = 无输入 / 退出信号（Ctrl+C / EOF）
    chat_id: str = ""  # 来源会话


class ApprovalRsp(BaseModel):
    approved: bool
    reason: str | None = None
