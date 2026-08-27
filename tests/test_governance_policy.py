"""Pure policy-order and fail-closed tests for Phase 6 Step 003."""

import pytest

from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.governance.policy import (
    GateResult,
    evaluate_gate_sequence,
    stricter_outcome,
)


def test_gate_sequence_stops_sensitive_checks_after_identity_failure() -> None:
    called: list[str] = []

    def identity() -> GateResult:
        called.append("identity")
        return GateResult("FAIL", "session does not match Run")

    def scope() -> GateResult:
        called.append("scope")
        return GateResult("PASS", "should not run")

    def permission() -> GateResult:
        called.append("permission")
        return GateResult("PASS", "should not run")

    results = evaluate_gate_sequence(
        [("identity", identity), ("scope", scope), ("permission", permission)]
    )
    assert called == ["identity"]
    assert results == {
        "identity": GateResult("FAIL", "session does not match Run"),
        "scope": GateResult("UNKNOWN", "blocked by identity"),
        "permission": GateResult("UNKNOWN", "blocked by identity"),
    }


def test_unknown_gate_result_fails_closed_and_blocks_following_gate() -> None:
    called: list[str] = []

    def permission() -> GateResult:
        called.append("permission")
        return GateResult("PASS", "bad")

    results = evaluate_gate_sequence(
        [
            ("identity", lambda: GateResult("PASS", "ok")),
            ("scope", lambda: GateResult("UNKNOWN", "scope cannot be verified")),
            ("permission", permission),
        ]
    )
    assert called == []
    assert results["scope"].status == "UNKNOWN"
    assert results["permission"].status == "UNKNOWN"


def test_stricter_workflow_result_wins() -> None:
    assert stricter_outcome("PASS", "PASS") == "PASS"
    assert stricter_outcome("PASS", "FAIL") == "FAIL"
    assert stricter_outcome("PASS", "UNKNOWN") == "UNKNOWN"
    assert stricter_outcome("FAIL", "PASS") == "FAIL"


def test_gate_result_rejects_unknown_status() -> None:
    with pytest.raises(GatewayFault):
        GateResult("MAYBE", "not a contract result")
