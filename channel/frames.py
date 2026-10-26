"""通道帧协议：帧类型与错误码常量（server / web bridge / 客户端共用）。"""

from enum import StrEnum


class Frame(StrEnum):
    """一帧 JSON 的 type 取值。"""

    # 传输包装
    NOTIFY = "notify"
    CALL = "call"
    REPLY = "reply"
    # 握手
    HELLO = "hello"
    WELCOME = "welcome"
    ERROR = "error"
    # 只读浏览（连接用完即关）
    LIST_ACTIVE = "list_active"
    ACTIVE_SESSIONS = "active_sessions"
    LIST_WORKSPACES = "list_workspaces"
    WORKSPACES = "workspaces"
    LIST_SESSIONS = "list_sessions"
    SESSIONS = "sessions"
    # 会话 / 运行控制
    INPUT_CREATE = "input.create"
    RUN_CANCEL = "run.cancel"
    SESSION_SET_AGENT = "session.set_agent"
    SESSION_SET_METADATA = "session.set_metadata"


class ErrorCode(StrEnum):
    """错误帧的 code 取值（机器可读，客户端按此分支）。"""

    INVALID_CWD = "invalid_cwd"
    SESSION_NOT_FOUND = "session_not_found"
    SESSION_TAKEN_OVER = "session_taken_over"


# 会话/运行控制帧的前缀：用于把控制类异常与普通断连区分开
CONTROL_PREFIXES = ("session.", "run.")
