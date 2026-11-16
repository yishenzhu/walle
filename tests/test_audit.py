"""审计日志与审批记录的回归测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..conf import ApprovalConfig, ApprovalDecision, ApprovalRuleSpec
from ..infra import auditlog as audit_module
from ..spec import PreExecuteToolEvent
from ..tools.approval import Approval


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_audit_writes_json_line(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_module, "LOGS", tmp_path)

    audit_module.audit("tool", tool="read", status="ok")

    events = read_events(tmp_path / "audit.jsonl")
    assert events == [{"ts": events[0]["ts"], "event": "tool", "tool": "read", "status": "ok"}]


def test_audit_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_module, "LOGS", tmp_path)
    monkeypatch.setenv("WALLE_AUDIT", "0")

    audit_module.audit("tool", tool="read")

    assert not (tmp_path / "audit.jsonl").exists()


def test_audit_write_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(audit_module, "LOGS", Path("/proc/walle-no-such-dir"))

    audit_module.audit("tool", tool="read")


def test_approval_denied_by_policy_is_audited(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_module, "LOGS", tmp_path)
    config = ApprovalConfig(
        rules=[ApprovalRuleSpec(ApprovalDecision.DENY, "bash")],
        default=ApprovalDecision.ALLOW,
    )

    verdict = asyncio.run(
        Approval(config).check(
            PreExecuteToolEvent(name="bash", arguments={"cmd": "ls"}, tool_call_id="1")
        )
    )

    assert verdict is not None and verdict.blocks
    events = read_events(tmp_path / "audit.jsonl")
    assert events[0]["event"] == "approval"
    assert events[0]["decision"] == "deny"
    assert events[0]["source"] == "policy"


def test_approval_allowed_by_policy_is_audited(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_module, "LOGS", tmp_path)
    config = ApprovalConfig(
        rules=[ApprovalRuleSpec(ApprovalDecision.ALLOW, "read")],
        default=ApprovalDecision.DENY,
    )

    verdict = asyncio.run(
        Approval(config).check(
            PreExecuteToolEvent(name="read", arguments={}, tool_call_id="2")
        )
    )

    assert verdict is None
    events = read_events(tmp_path / "audit.jsonl")
    assert events[0]["decision"] == "allow"
