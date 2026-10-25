"""通道载荷：notify（广播无返回）与 call（点对点有返回）。

载荷按语义命名，类别由 Channel 方法表达；判别联合按 type 区分。"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ── 连接与交互载荷（会话建立 / 用户输入 / 审批回复）──────────────


class ModelConfig(BaseModel):
    """模型接入配置：OpenAI 兼容端点 + 模型名。

    三项齐全才生效；缺省回退进程默认 provider。"""

    api_key: str
    base_url: str
    model: str


class SessionSpec(BaseModel):
    """新建会话规格：session_id、cwd、模型配置与 agent 名。

    model 缺省时回退进程默认 provider；agent 缺省为 default。"""

    session_id: str
    cwd: str | None  # 客户端工作目录（bash 执行 / 沙箱可写区基准）
    model: ModelConfig | None  # 模型接入配置（None = 用默认）
    agent: str | None = None  # agent 名（None = default）；创建会话时按名加载


class UserInput(BaseModel):
    content: str | None = None  # None = 无输入 / 退出信号（Ctrl+C / EOF）
    session_id: str = ""  # 来源会话


class ApprovalResponse(BaseModel):
    approved: bool
    reason: str | None = None


# ── 通知（广播，无返回）────────────────────────────────────────


class Notification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    session_id: str = ""  # 目标会话（路由字段）


class Delta(Notification):
    """流式文本增量。"""

    type: Literal["delta"] = "delta"
    delta: str


class DeltaEnd(Notification):
    """流式输出结束（回合边界）；reason 为机器可读终态原因。"""

    type: Literal["delta_end"] = "delta_end"
    reason: str | None = None
    detail: str | None = None


class ToolPrepare(Notification):
    """模型正在生成工具调用参数（工具尚未开始执行）。"""

    type: Literal["tool_prepare"] = "tool_prepare"
    tool_name: str


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
    """Agent / 通道错误。code 供客户端区分忙、断开等状态。"""

    type: Literal["error"] = "error"
    message: str
    code: str = ""


NotificationUnion = Annotated[
    Delta | DeltaEnd | ToolPrepare | ToolStart | ToolResult | Error,
    Field(discriminator="type"),
]


# ── 服务（点对点，有返回）──────────────────────────────────────


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    session_id: str = ""  # 目标会话（路由字段）


class Inquiry(Request):
    """向用户提问（ask_user 工具）。返回 str。"""

    type: Literal["inquiry"] = "inquiry"
    question: str
    options: list[str] | None = None


class ApprovalRequest(Request):
    """请求工具执行审批。返回 ApprovalResponse。"""

    type: Literal["approval"] = "approval"
    tool_name: str
    arguments: dict[str, Any]
    tool_call_id: str  # 工具调用 id，卡片按钮回调按此路由


RequestUnion = Annotated[
    Inquiry | ApprovalRequest,
    Field(discriminator="type"),
]
