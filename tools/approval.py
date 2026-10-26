"""审批插件：工具审批（规则判断 + 人工审批）作为插件声明。"""

import fnmatch
import logging
from typing import Any

from pydantic import BaseModel

from ..conf import ApprovalConfig, ApprovalDecision, ApprovalRuleSpec
from ..infra import HookVerdict, tool_context
from ..plugin import plugin
from ..spec import ApprovalRequest, ApprovalResponse, Channel, PreExecuteToolEvent

logger = logging.getLogger(__name__)


class ArgMatch(BaseModel):
    name: str
    pattern: str


class ApprovalRule(BaseModel):
    action: ApprovalDecision
    name_pattern: str
    arg_matches: list[ArgMatch] = []

    @classmethod
    def parse(cls, spec: ApprovalRuleSpec):
        pattern = spec.pattern
        if "(" in pattern:
            if not pattern.endswith(")"):
                raise ValueError(f"Invalid pattern (missing ')'): {pattern}")
            idx = pattern.index("(")
            name_pattern = pattern[:idx].strip()
            if not name_pattern:
                raise ValueError(f"Invalid pattern (empty tool name): {pattern}")
            arg_part = pattern[idx + 1 : -1]
            return cls(
                action=spec.action,
                name_pattern=name_pattern,
                arg_matches=cls._parse_args(arg_part),
            )
        return cls(action=spec.action, name_pattern=pattern.strip(), arg_matches=[])

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


class ChannelApprover:
    """交互式审批：通过 Channel 发起 ApprovalRequest 服务。"""

    def __init__(self, channel: Channel):
        self._channel = channel

    async def ask(self, tool_name: str, arguments: dict, tool_call_id: str) -> ApprovalResponse:
        data = await self._channel.call(
            ApprovalRequest(
                tool_name=tool_name, arguments=arguments, tool_call_id=tool_call_id
            )
        )
        # 真实通道（如 CLI）的 call 返回 JSON 反序列化后的 dict，需验证为模型；
        # 测试 / 内存通道可能直接返回 ApprovalResponse 实例，原样透传。
        return (
            data
            if isinstance(data, ApprovalResponse)
            else ApprovalResponse.model_validate(data)
        )


class Approval:
    """审批插件：把工具审批（规则判断 + 人工审批）作为插件提供。"""

    def __init__(self, config: ApprovalConfig | None = None):
        self._policy = ApprovalPolicy(config or ApprovalConfig())

    async def check(self, evt: PreExecuteToolEvent, value=None) -> HookVerdict | None:
        """审批屏障：规则判断 + ASK 时经 channel 人工确认。"""
        name = evt.name
        args = evt.arguments
        tc_id = evt.tool_call_id

        decision = self._policy.evaluate(name, args)
        if decision == ApprovalDecision.DENY:
            return HookVerdict(block=f"Tool '{name}' denied by policy")
        if decision == ApprovalDecision.ALLOW:
            return None

        # ASK：经执行上下文（tool_context 提前注入）的 channel 问用户
        session = tool_context.get()
        if session is None or session.channel is None:
            return HookVerdict(block=f"Tool '{name}' denied: no approval channel")
        try:
            response = await ChannelApprover(session.channel).ask(name, args, tc_id)
        except Exception as exc:
            logger.warning(f"approval for '{name}' failed: {exc}")
            return HookVerdict(block=f"Tool '{name}' denied: approval failed ({exc})")
        if response.approved:
            return None
        reason = f"Tool '{name}' denied by user"
        return HookVerdict(
            block=f"{reason}: {response.reason}" if response.reason else reason
        )



@plugin(name="approval")
async def native_approval(ctx, config) -> None:
    """审批插件：订阅工具预执行事件，拒绝即阻断。"""
    approval = Approval(config.get("config"))
    ctx.on(PreExecuteToolEvent, approval.check)
