"""Deterministic tests for the Phase 10 LAB_ONLY orchestration comparison."""

from __future__ import annotations

from typing import NoReturn

import pytest
from agent_runtime.providers import ProviderError, ProviderRole

from labs.p2p_orchestration import phase10_comparison as comparison
from labs.p2p_orchestration.phase10_comparison import (
    ACTIONS,
    EVENT_MATRIX,
    REAL_CASES,
    STRATEGIES,
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


def test_real_report_keeps_fixed_matrix_and_blocks_missing_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_role: ProviderRole) -> NoReturn:
        raise ProviderError("provider unavailable", failure_code="INVALID_CONFIGURATION")

    monkeypatch.setattr(comparison, "provider_for_role", unavailable)
    report = run_phase10_comparison(mode="real")

    assert report.status == "BLOCKED"
    assert report.mode == "real"
    assert len(report.trials) == len(REAL_CASES) * len(STRATEGIES)
    assert [trial.case_id for trial in report.trials[:3]] == [
        REAL_CASES[0].case_id,
        REAL_CASES[0].case_id,
        REAL_CASES[0].case_id,
    ]
    assert [trial.strategy for trial in report.trials[:3]] == [
        "single_agent",
        "fixed_workflow",
        "multi_agent",
    ]
    assert all(
        trial.status == "BLOCKED_CONFIGURATION"
        for trial in report.trials
        if trial.strategy != "fixed_workflow"
    )
    assert all(
        trial.prompt_tokens is None
        and trial.completion_tokens is None
        and trial.reasoning_tokens is None
        for trial in report.trials
        if trial.strategy != "fixed_workflow"
    )
    fixed = next(row for row in report.results if row.strategy == "fixed_workflow")
    assert fixed.token_count == 0
    assert report.adoption == "KEEP_FIXED_WORKFLOW_BUSINESS_BASELINE"
