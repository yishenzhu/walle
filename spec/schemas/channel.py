from pydantic import BaseModel


class ModelConfig(BaseModel):
    """模型接入配置：一个 OpenAI 兼容端点 + 模型名（客户端随握手提供）。

    三项齐全才生效；缺省回退进程默认 provider。不同连接可指向不同端点。
    """

    api_key: str
    base_url: str
    model: str


class SessionConn(BaseModel):
    """新建会话的连接载荷：承载会话身份（chat_id）、工作目录（cwd）与模型配置。

    model 由客户端随握手提供——不同连接可指向不同端点；缺省（None）回退默认。
    """

    chat_id: str
    cwd: str | None  # 客户端工作目录（bash 执行 / 沙箱可写区基准）
    model: ModelConfig | None  # 模型接入配置（None = 用默认）


class UserInput(BaseModel):
    content: str | None = None  # None = 无输入 / 退出信号（Ctrl+C / EOF）
    chat_id: str = ""  # 来源会话


class ApprovalRsp(BaseModel):
    approved: bool
    reason: str | None = None
