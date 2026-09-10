"""诊断模型：资源加载的错误/警告记录。

错误不被静默吞掉：冲突直接抛错，扩展标记 FAILED；diagnostics 作为失败
原因的结构化描述供上层展示，不用于降级跳过。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DiagnosticType(StrEnum):
    WARNING = "warning"
    ERROR = "error"


@dataclass
class ResourceDiagnostic:
    """单条诊断：类型 + 消息 + 可选来源路径。"""

    type: DiagnosticType
    message: str
    path: str | None = None
