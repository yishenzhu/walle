import logging
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

import yaml
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = PROJECT_ROOT / ".agent"


def resolve_path(path: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return str(p.resolve())


class MCPConfig(BaseModel):
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    timeout: int | None = None  # 连接超时（秒），None 用 httpx 默认
    enabled: bool = True


class OTLPConfig(BaseModel):
    endpoint: str | None = None
    insecure: bool = True


class TelemetryConfig(BaseModel):
    enabled: bool = False
    service_name: str = PROJECT_ROOT.name
    otlp: OTLPConfig = OTLPConfig()
    console_export: bool = False


class LogConfig(BaseModel):
    level: str
    path: str
    backup_count: int

    @property
    def level_number(self) -> int:
        return getattr(logging, self.level.upper(), logging.DEBUG)


class ApprovalDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class ApprovalRuleSpec(NamedTuple):
    action: ApprovalDecision
    pattern: str


class ApprovalConfig(BaseModel):
    rules: list[ApprovalRuleSpec] = Field(default_factory=list)
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


class SessionConfig(BaseModel):
    """会话持久化配置：历史存储后端与路径。

    path 按后端解释：file = 会话根（缺省 data/sessions）；sqlite = db 文件
    （缺省 data/session.db）；memory 忽略。
    """

    storage: str = "file"  # file | sqlite | memory
    path: str | None = None


class PluginConfig(BaseModel):
    """插件加载配置：目录 + 启停名单（enabled 非空即白名单，disabled 优先）。"""

    dir: str = ".agent/plugins"  # 插件目录（相对项目根）
    enabled: list[str] = Field(default_factory=list)  # 空 = 全部启用
    disabled: list[str] = Field(default_factory=list)


class Config(BaseModel):
    log: LogConfig
    telemetry: TelemetryConfig = TelemetryConfig()
    tool: ToolConfig = ToolConfig()
    session: SessionConfig = SessionConfig()
    plugin: PluginConfig = PluginConfig()

    @classmethod
    def load(cls, path: str = "conf.yaml"):
        with open(resolve_path(path), encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return cls.model_validate(data)
