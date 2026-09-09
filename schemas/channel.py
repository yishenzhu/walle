from pydantic import BaseModel


class ModelConfig(BaseModel):
    """模型接入配置：一个 OpenAI 兼容端点 + 模型名（客户端随握手提供）。

    三项齐全才生效；缺省回退进程默认 provider。不同连接可指向不同端点。
    """

    api_key: str
    base_url: str
    model: str


class UserInput(BaseModel):
    content: str | None = None  # None = 无输入 / 退出信号（Ctrl+C / EOF）
    chat_id: str = ""  # 来源会话


class ApprovalRsp(BaseModel):
    approved: bool
    reason: str | None = None
