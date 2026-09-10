"""统一底层契约包：领域数据（schemas）+ 能力协议（protocol）。

- schemas/   纯数据模型（BaseModel / 结构性标注）：Message、Usage、SessionConn…
- protocol/  能力接口（Protocol）：Messages、Channel、Sessions、ToolTable…

依赖方向唯一：protocol → schemas；两者都不引用具体实现，无成环。
使用方统一从 spec 顶层导入，装配根向具体实现注入。
"""

from .schemas import (
    SessionConn,
    ModelConfig,
    UserInput,
    ApprovalRsp,
    DiagnosticType,
    ResourceDiagnostic,
    Message,
    UserMessage,
    SystemMessage,
    ToolMessage,
    AssistantMessage,
    MessageAdapter,
    Usage,
    Notification,
    Delta,
    DeltaEnd,
    ToolStart,
    ToolResult,
    Error,
    NotificationUnion,
    Service,
    Receive,
    Inquiry,
    Approval,
    ServiceUnion,
    JobDispatch,
    JobResult,
)
from .protocol import (
    Channel,
    Sessions,
    Messages,
    Projection,
    ProjectionStore,
    ToolTable,
)

__all__ = [
    # schemas — 领域数据
    "SessionConn",
    "ModelConfig",
    "UserInput",
    "ApprovalRsp",
    "DiagnosticType",
    "ResourceDiagnostic",
    "Message",
    "UserMessage",
    "SystemMessage",
    "ToolMessage",
    "AssistantMessage",
    "MessageAdapter",
    "Usage",
    "Notification",
    "Delta",
    "DeltaEnd",
    "ToolStart",
    "ToolResult",
    "Error",
    "NotificationUnion",
    "Service",
    "Receive",
    "Inquiry",
    "Approval",
    "ServiceUnion",
    "JobDispatch",
    "JobResult",
    # protocol — 能力协议
    "Channel",
    "Sessions",
    "Messages",
    "Projection",
    "ProjectionStore",
    "ToolTable",
]