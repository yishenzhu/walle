"""通道载荷：Channel 协议 notify / call 两个原语的消息模型。

- 通知（Notification）：广播，无返回；对应 JSON-RPC 2.0 的无 id 消息。
- 服务（Service）：点对点，有返回；对应带 id 的 Request/Response。

命名约定：类名不带 Notification / Service 后缀，类别由 Channel 方法表达，
如 `channel.notify(Delta(...))`、`channel.call(Receive())`。判别联合
（NotificationUnion / ServiceUnion）按 `type` 字段区分。

会话建立的连接载荷（SessionConn / ModelConfig）与交互返回（UserInput /
ApprovalRsp）同属通道契约，故一并置于本模块。
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ── 连接与交互载荷（会话建立 / 用户输入 / 审批回复）──────────────


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


# ── 通知（广播，无返回）────────────────────────────────────────


class Notification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    chat_id: str = ""  # 目标会话（路由字段）


class Delta(Notification):
    """流式文本增量。"""

    type: Literal["delta"] = "delta"
    delta: str


class DeltaEnd(Notification):
    """流式输出结束（回合边界）。"""

    type: Literal["delta_end"] = "delta_end"


class ToolStart(Notification):
    """工具开始执行。"""

    type: Literal["tool_start"] = "tool_start"
    tool_name: str
    arguments: dict[str, Any]
    tool_call_id: str


class ToolResult(Notification):
    """工具结果（成功或失败，error 区分）。"""

    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    result: Any = None
    error: str | None = None  # 非空表示失败：被拒 / 超时 / 异常


class Error(Notification):
    """Agent 层错误。"""

    type: Literal["error"] = "error"
    message: str


NotificationUnion = Annotated[
    Delta | DeltaEnd | ToolStart | ToolResult | Error,
    Field(discriminator="type"),
]


# ── 服务（点对点，有返回）──────────────────────────────────────


class Service(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    chat_id: str = ""  # 目标会话（路由字段）


class Receive(Service):
    """读用户输入。返回 UserInput。"""

    type: Literal["receive"] = "receive"


class Inquiry(Service):
    """向用户提问（ask_user 工具）。返回 str。"""

    type: Literal["inquiry"] = "inquiry"
    question: str
    options: list[str] | None = None


class Approval(Service):
    """请求工具执行审批。返回 ApprovalRsp。"""

    type: Literal["approval"] = "approval"
    tool_name: str
    arguments: dict[str, Any]
    tool_call_id: str  # 工具调用 id，卡片按钮回调按此路由


ServiceUnion = Annotated[
    Receive | Inquiry | Approval,
    Field(discriminator="type"),
]
