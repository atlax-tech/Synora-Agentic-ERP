"""Deterministic tests for the Phase 10 LAB_ONLY orchestration comparison."""

from __future__ import annotations

from labs.p2p_orchestration.phase10_comparison import (
    ACTIONS,
    EVENT_MATRIX,
    report_as_json,
    run_phase10_comparison,
)


def test_phase10_comparison_is_replayable_and_uses_one_fixed_dataset() -> None:
    first = run_phase10_comparison()
    second = run_phase10_comparison()

    assert first == second
    assert first.status == "PASS"
    assert first.dataset_digest == second.dataset_digest
    assert len(first.event_matrix) == len(EVENT_MATRIX)
    assert tuple(first.initial_erp_facts) == tuple(second.initial_erp_facts)


def test_fixed_workflow_recovers_duplicates_and_restart_without_event_authority() -> None:
    report = run_phase10_comparison()
    rows = {row.strategy: row for row in report.results}

    fixed = rows["fixed_workflow"]
    multi = rows["multi_agent"]
    baseline = rows["single_agent"]
    assert fixed.completed_actions == ACTIONS
    assert fixed.safety_violations == 0
    assert fixed.recovery_rate == 1.0
    assert fixed.ignored_duplicate_events == 2
    assert fixed.restart_recovered is True
    assert multi.safety_violations == 0
    assert multi.recovery_rate == 1.0
    assert baseline.safety_violations > 0
    assert baseline.status == "FAILED"

    for event in report.event_matrix:
        assert event["authorization_hint"] == "spoofed-event-data"
    assert report.artifact_policy.startswith("recorded test double only")


def test_report_json_is_bounded_and_does_not_contain_credentials_or_prompts() -> None:
    payload = report_as_json(run_phase10_comparison())
    rendered = str(payload)
    assert "capability" not in rendered.lower()
    assert "password" not in rendered.lower()
    assert "prompt" not in rendered.lower()
