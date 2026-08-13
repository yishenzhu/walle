from typing import NamedTuple

import logging
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

PROJ_ROOT = Path(__file__).resolve().parent.parent
DOT_AGENT = PROJ_ROOT / ".agent"


def auto_path(path: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = PROJ_ROOT / p
    return str(p.resolve())


class MCPConfig(BaseModel):
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    timeout: int | None = None   # 连接超时（秒），None 用 httpx 默认
    enabled: bool = True


class OTLPConfig(BaseModel):
    endpoint: str | None = None
    insecure: bool = True


class TelemetryConfig(BaseModel):
    enabled: bool = False
    service_name: str = PROJ_ROOT.name
    otlp: OTLPConfig = OTLPConfig()
    console_export: bool = False


class LogConfig(BaseModel):
    level: str
    path: str
    backup_count: int

    @property
    def level_int(self) -> int:
        return getattr(logging, self.level.upper(), logging.DEBUG)


class ApprovalDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class RawRule(NamedTuple):
    action: ApprovalDecision
    pattern: str


class ApprovalConfig(BaseModel):
    rules: list[RawRule] = Field(default_factory=list)
    default: ApprovalDecision = ApprovalDecision.ASK


class TimeoutConfig(BaseModel):
    """工具超时：全局默认 + 按工具名覆盖（None = 豁免超时）。"""

    default: float | None = 30.0
    overrides: dict[str, float | None] = Field(default_factory=dict)

    def resolve(self, name: str) -> float | None:
        """按工具名匹配 overrides > 全局 default。"""
        if name in self.overrides:
            return self.overrides[name]
        return self.default


class ToolConfig(BaseModel):
    approval: ApprovalConfig = ApprovalConfig()
    timeout: TimeoutConfig = TimeoutConfig()


class Config(BaseModel):
    log: LogConfig
    telemetry: TelemetryConfig = TelemetryConfig()
    tool: ToolConfig = ToolConfig()

    @classmethod
    def load(cls, path: str = "conf.yaml"):
        with open(auto_path(path), encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return cls.model_validate(data)
