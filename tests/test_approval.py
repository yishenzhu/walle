"""ApprovalRule 解析与 ApprovalPolicy 匹配测试。"""
import pytest

from ..conf import ApprovalConfig, ApprovalDecision, RawRule
from ..core.approval import ApprovalRule, ApprovalPolicy


# ── ApprovalRule.parse ────────────────────────────────

class TestRuleParse:
    def test_plain_name(self):
        rule = ApprovalRule.parse(RawRule(ApprovalDecision.ALLOW, "bash"))
        assert rule.name_pattern == "bash"
        assert rule.arg_matches == []

    def test_name_with_args(self):
        rule = ApprovalRule.parse(
            RawRule(ApprovalDecision.DENY, "bash(cmd=rm -rf *)")
        )
        assert rule.name_pattern == "bash"
        assert len(rule.arg_matches) == 1
        assert rule.arg_matches[0].name == "cmd"
        assert rule.arg_matches[0].pattern == "rm -rf *"

    def test_multiple_args(self):
        rule = ApprovalRule.parse(
            RawRule(ApprovalDecision.ALLOW, "search(query=*, limit=10)")
        )
        assert rule.name_pattern == "search"
        assert len(rule.arg_matches) == 2
        assert rule.arg_matches[0].name == "query"
        assert rule.arg_matches[1].name == "limit"

    def test_wildcard_arg_name(self):
        rule = ApprovalRule.parse(
            RawRule(ApprovalDecision.DENY, "bash(*=rm -rf *)")
        )
        assert rule.arg_matches[0].name == "*"

    def test_missing_closing_paren_raises(self):
        with pytest.raises(ValueError, match="missing '\\)'"):
            ApprovalRule.parse(RawRule(ApprovalDecision.DENY, "bash(cmd=test"))

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="empty tool name"):
            ApprovalRule.parse(RawRule(ApprovalDecision.DENY, "(cmd=test)"))

    def test_arg_without_equals_raises(self):
        with pytest.raises(ValueError, match="must be 'name=pattern'"):
            ApprovalRule.parse(RawRule(ApprovalDecision.DENY, "bash(nopattern)"))

    def test_empty_arg_string(self):
        rule = ApprovalRule.parse(RawRule(ApprovalDecision.ALLOW, "bash()"))
        assert rule.arg_matches == []


# ── ApprovalRule.match ────────────────────────────────

class TestRuleMatch:
    def test_name_only_match(self):
        rule = ApprovalRule.parse(RawRule(ApprovalDecision.ALLOW, "bash"))
        assert rule.match("bash", {"cmd": "ls"}) is True

    def test_name_glob_match(self):
        rule = ApprovalRule.parse(RawRule(ApprovalDecision.ALLOW, "mcp_*"))
        assert rule.match("mcp_rag", {}) is True
        assert rule.match("bash", {}) is False

    def test_arg_match(self):
        rule = ApprovalRule.parse(RawRule(ApprovalDecision.DENY, "bash(cmd=rm -rf *)"))
        assert rule.match("bash", {"cmd": "rm -rf /"}) is True
        assert rule.match("bash", {"cmd": "ls -la"}) is False

    def test_arg_mismatch_returns_false(self):
        rule = ApprovalRule.parse(RawRule(ApprovalDecision.ALLOW, "bash(cmd=ls *)"))
        assert rule.match("bash", {"cmd": "rm -rf /"}) is False

    def test_wildcard_arg_matches_any_value(self):
        rule = ApprovalRule.parse(RawRule(ApprovalDecision.DENY, "bash(*=rm -rf *)"))
        assert rule.match("bash", {"cmd": "rm -rf /"}) is True
        assert rule.match("bash", {"unknown_arg": "rm -rf /"}) is True
        assert rule.match("bash", {"cmd": "ls"}) is False

    def test_missing_arg_returns_false(self):
        rule = ApprovalRule.parse(RawRule(ApprovalDecision.ALLOW, "bash(cmd=ls *)"))
        assert rule.match("bash", {}) is False

    def test_none_arg_value_returns_false(self):
        rule = ApprovalRule.parse(RawRule(ApprovalDecision.ALLOW, "bash(cmd=ls *)"))
        assert rule.match("bash", {"cmd": None}) is False


# ── ApprovalPolicy.evaluate ───────────────────────────

class TestPolicyEvaluate:
    def test_default_ask(self):
        policy = ApprovalPolicy()
        assert policy.evaluate("bash", {"cmd": "ls"}) == ApprovalDecision.ASK

    def test_default_allow(self):
        policy = ApprovalPolicy(ApprovalConfig(default=ApprovalDecision.ALLOW))
        assert policy.evaluate("bash", {}) == ApprovalDecision.ALLOW

    def test_first_match_wins(self):
        policy = ApprovalPolicy(
            ApprovalConfig(
                rules=[
                    RawRule(ApprovalDecision.DENY, "bash(cmd=rm -rf *)"),
                    RawRule(ApprovalDecision.ALLOW, "bash"),
                ],
            )
        )
        assert policy.evaluate("bash", {"cmd": "rm -rf /"}) == ApprovalDecision.DENY
        assert policy.evaluate("bash", {"cmd": "ls"}) == ApprovalDecision.ALLOW

    def test_no_match_returns_default(self):
        policy = ApprovalPolicy(
            ApprovalConfig(
                rules=[RawRule(ApprovalDecision.DENY, "bash")],
                default=ApprovalDecision.ALLOW,
            )
        )
        assert policy.evaluate("ask_user", {}) == ApprovalDecision.ALLOW
