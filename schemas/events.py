"""统一消息模型：通知（广播、无返回）与服务（点对点、有返回）。

对应 JSON-RPC 2.0：Notification（无 id）与 Request/Response（带 id）。
命名约定：事件类名不带 Notification / Service 后缀，类别由 Channel 方法
（notify / call）表达，如 channel.notify(Delta(...))、channel.call(Receive())。
"""
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


class JobDispatch(BaseModel):
    """background 工具返回：作业派发结果，成功即拿到 job_id。"""

    job_id: str
    status: str = "running"   # 派发成功即 running（待 executor 拉起）
    error: str | None = None   # 非空表示派发失败


class JobResult(BaseModel):
    """job_result 工具返回：作业状态与结果。"""

    job_id: str
    status: str                 # running | done | error
    result: Any = None          # done：执行结果
    error: str | None = None    # error：错误信息
