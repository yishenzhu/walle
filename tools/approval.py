"""审批扩展：工具审批（规则判断 + 人工审批）作为扩展声明。

Approval(config).as_ext 与 mcp/skill 同形态——main 里作为 "approval"
扩展加进进程级扩展池，每个会话激活后订阅 TOOL_EXECUTION_START。
"""

import asyncio
import fnmatch
from typing import Any, Protocol

from pydantic import BaseModel

from ..conf import ApprovalConfig, ApprovalDecision, RawRule
from ..infra import (
    ExtensionAPI,
    HookVerdict,
    ToolExecutionStartEvent,
    tool_context,
)
from ..spec import Approval as ApprovalService
from ..spec import ApprovalRsp, Channel


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

    async def ask(self, tool_name: str, arguments: dict, tool_call_id: str) -> ApprovalRsp: ...


class ChannelApprover:
    """交互式审批：通过 Channel 发起 Approval 服务。"""

    def __init__(self, channel: Channel):
        self._channel = channel

    async def ask(self, tool_name: str, arguments: dict, tool_call_id: str) -> ApprovalRsp:
        data = await self._channel.call(
            ApprovalService(
                tool_name=tool_name, arguments=arguments, tool_call_id=tool_call_id
            )
        )
        # 真实通道（如 CLI）的 call 返回 JSON 反序列化后的 dict，需验证为模型；
        # 测试 / 内存通道可能直接返回 ApprovalRsp 实例，原样透传。
        return (
            data
            if isinstance(data, ApprovalRsp)
            else ApprovalRsp.model_validate(data)
        )


class AutoApproveApprover:
    """静默放行（测试 / 无人值守）。"""

    async def ask(self, tool_name: str, arguments: dict, tool_call_id: str) -> ApprovalRsp:
        return ApprovalRsp(approved=True)


class DenyApprover:
    """静默拒绝（测试拒绝路径）。"""

    async def ask(self, tool_name: str, arguments: dict, tool_call_id: str) -> ApprovalRsp:
        return ApprovalRsp(approved=False, reason="auto denied")


class TimeoutApprover:
    """装饰器：超时未答复自动拒绝，防止卡死。"""

    def __init__(self, inner: Approver, timeout: float):
        self._inner, self._timeout = inner, timeout

    async def ask(self, tool_name: str, arguments: dict, tool_call_id: str) -> ApprovalRsp:
        try:
            return await asyncio.wait_for(
                self._inner.ask(tool_name, arguments, tool_call_id), self._timeout
            )
        except TimeoutError:
            return ApprovalRsp(approved=False, reason=f"审批超时({self._timeout}s)")


class Approval:
    """审批扩展：把工具审批（规则判断 + 人工审批）作为扩展提供。

    订阅 TOOL_EXECUTION_START——内部用 ApprovalPolicy（conf 规则）做
    deny/allow/ask 判断；ASK 时经 tool_context 的 channel 发起人工审批
    （Approval 服务），拒绝即 HookVerdict(block)。

    main 组装：extensions.add("approval", Approval(conf.tool.approval).as_ext)。
    executor 不再内置审批——preflight 事件是唯一审批屏障，审批策略可被替换/覆盖。
    """

    def __init__(self, config: ApprovalConfig | None = None):
        self._policy = ApprovalPolicy(config or ApprovalConfig())

    async def as_ext(self, api: ExtensionAPI) -> None:
        """把审批 handler 注册进扩展 api（与 mcp/skill 的 as_ext 同形态）。"""
        async def check(evt: ToolExecutionStartEvent) -> HookVerdict | None:
            name = evt.tool_name
            args = evt.arguments
            tc_id = evt.tool_call_id

            decision = self._policy.evaluate(name, args)
            if decision == ApprovalDecision.DENY:
                return HookVerdict(block=f"Tool '{name}' denied by policy")
            if decision == ApprovalDecision.ALLOW:
                return None

            # ASK：经执行上下文（tool_context 提前注入）的 channel 问用户
            ctx = tool_context.get()
            if ctx is None or ctx.channel is None:
                return HookVerdict(block=f"Tool '{name}' denied: no approval channel")
            response = await ChannelApprover(ctx.channel).ask(name, args, tc_id)
            if response.approved:
                return None
            reason = f"Tool '{name}' denied by user"
            return HookVerdict(
                block=f"{reason}: {response.reason}" if response.reason else reason
            )

        api.on(ToolExecutionStartEvent, check)
