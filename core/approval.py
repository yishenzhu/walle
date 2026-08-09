import asyncio
import fnmatch
from typing import Any, Protocol

from pydantic import BaseModel

from ..channel import Channel
from ..conf import ApprovalConfig, ApprovalDecision, RawRule
from ..schemas import Approval, ApprovalRsp


class ArgMatch(BaseModel):
    name: str
    pattern: str


class ApprovalRule(BaseModel):
    action: ApprovalDecision
    name_pattern: str
    arg_matches: list[ArgMatch] = []

    @classmethod
    def parse(cls, raw: RawRule):
        pattern = raw.pattern
        if "(" in pattern:
            if not pattern.endswith(")"):
                raise ValueError(f"Invalid pattern (missing ')'): {pattern}")
            idx = pattern.index("(")
            name_pattern = pattern[:idx].strip()
            if not name_pattern:
                raise ValueError(f"Invalid pattern (empty tool name): {pattern}")
            arg_part = pattern[idx + 1 : -1]
            return cls(
                action=raw.action,
                name_pattern=name_pattern,
                arg_matches=cls._parse_args(arg_part),
            )
        return cls(action=raw.action, name_pattern=pattern.strip(), arg_matches=[])

    @staticmethod
    def _parse_args(pattern: str) -> list[ArgMatch]:
        if not pattern:
            return []
        matches: list[ArgMatch] = []
        for pair in pattern.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if "=" not in pair:
                raise ValueError(f"Arg pattern must be 'name=pattern', got: '{pair}'")
            name, _, p = pair.partition("=")
            matches.append(ArgMatch(name=name.strip(), pattern=p.strip()))
        return matches

    def match(self, tool_name: str, args: dict[str, Any]) -> bool:
        if not fnmatch.fnmatch(tool_name, self.name_pattern):
            return False
        if not self.arg_matches:
            return True
        return all(self._match_arg(am, args) for am in self.arg_matches)

    @staticmethod
    def _match_arg(am: ArgMatch, args: dict[str, Any]) -> bool:
        if am.name == "*":
            return any(
                fnmatch.fnmatch(str(v), am.pattern)
                for v in args.values()
                if v is not None
            )
        v = args.get(am.name)
        return v is not None and fnmatch.fnmatch(str(v), am.pattern)


class ApprovalPolicy:
    def __init__(self, config: ApprovalConfig | None = None):
        cfg = config or ApprovalConfig()
        self._rules: list[ApprovalRule] = [ApprovalRule.parse(r) for r in cfg.rules]
        self._default = ApprovalDecision(cfg.default)

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> ApprovalDecision:
        for rule in self._rules:
            if rule.match(tool_name, args):
                return rule.action
        return self._default


class Approver(Protocol):
    """审批策略：决定要不要问、怎么问（底层发起 Approval 服务）。"""

    async def ask(self, tool_name: str, arguments: dict) -> ApprovalRsp: ...


class ChannelApprover:
    """交互式审批：通过 Channel 发起 Approval 服务。"""

    def __init__(self, channel: Channel):
        self._channel = channel

    async def ask(self, tool_name: str, arguments: dict) -> ApprovalRsp:
        return await self._channel.call(
            Approval(tool_name=tool_name, arguments=arguments)
        )


class AutoApproveApprover:
    """静默放行（测试 / 无人值守）。"""

    async def ask(self, tool_name: str, arguments: dict) -> ApprovalRsp:
        return ApprovalRsp(approved=True)


class DenyApprover:
    """静默拒绝（测试拒绝路径）。"""

    async def ask(self, tool_name: str, arguments: dict) -> ApprovalRsp:
        return ApprovalRsp(approved=False, reason="auto denied")


class TimeoutApprover:
    """装饰器：超时未答复自动拒绝，防止卡死。"""

    def __init__(self, inner: Approver, timeout: float):
        self._inner, self._timeout = inner, timeout

    async def ask(self, tool_name: str, arguments: dict) -> ApprovalRsp:
        try:
            return await asyncio.wait_for(
                self._inner.ask(tool_name, arguments), self._timeout
            )
        except asyncio.TimeoutError:
            return ApprovalRsp(approved=False, reason=f"审批超时({self._timeout}s)")
