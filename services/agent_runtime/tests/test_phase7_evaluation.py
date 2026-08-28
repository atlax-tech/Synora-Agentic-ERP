"""Phase 7 same-task recorded comparison and adoption evidence tests."""

import json

from agent_runtime.evaluation.phase7 import run_phase7_recorded_suite


def test_recorded_phase7_suite_preserves_safety_and_task_results() -> None:
    report = run_phase7_recorded_suite()

    assert report.schema_version == "1"
    assert report.real_provider_executed is False
    assert report.all_safety_passed is True
    assert all(
        record.task_success for record in report.records if record.variant != "BUDGET_FAILURE"
    )
    assert {record.variant for record in report.records} >= {
        "PROMPT_A",
        "PROMPT_B",
        "SKILLS_ON",
        "SKILLS_OFF",
        "BUDGET_FAILURE",
    }
    assert all(comparison.non_decision_layers_equal for comparison in report.prompt_comparisons)
    assert all(comparison.decision == "RETAIN_A" for comparison in report.prompt_comparisons)
    assert all(comparison.task_success_not_degraded for comparison in report.skill_comparisons)


def test_recorded_phase7_context_budget_and_malicious_skill_evidence() -> None:
    report = run_phase7_recorded_suite()

    assert all(
        evidence.estimated_before > evidence.estimated_after
        and evidence.estimated_after <= evidence.input_budget
        and evidence.native_compression_applied
        and evidence.native_estimated_before > evidence.native_estimated_after
        and evidence.native_estimated_after <= evidence.native_input_budget
        and evidence.native_actual_prompt_tokens is not None
        and evidence.task_success_not_degraded
        and evidence.security_prompt_preserved
        and evidence.latest_observation_preserved
        and evidence.all_evidence_digests_preserved
        for evidence in report.context_compressions
    )
    assert all(
        record.stop_reason == "CONTEXT_BUDGET" and record.provider_calls == 0
        for record in report.records
        if record.variant == "BUDGET_FAILURE"
    )
    assert all(
        check.write_tool_schema_absent and check.provider_calls == 0
        for check in report.malicious_skill_checks
    )
    assert {decision.status for decision in report.adoption_card} >= {
        "ADOPTED",
        "CONDITIONAL",
        "DEFERRED",
        "REJECTED",
    }
    encoded = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    assert "purchase.submit" not in encoded
    assert "Ignore previous" not in encoded
