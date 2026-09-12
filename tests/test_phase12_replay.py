from __future__ import annotations

from labs.self_improvement.data import build_synthetic_manifest
from labs.self_improvement.replay import (
    candidate_policy,
    deterministic_policy,
    initial_state,
    policy_from_actions,
    run_replay,
)
from labs.self_improvement.training import state_features


def test_deterministic_policy_passes_one_case_of_each_kind() -> None:
    manifest = build_synthetic_manifest("replay-test")
    cases = {case.kind: case for case in manifest.cases if case.split == "train"}
    results = [run_replay(case, deterministic_policy) for case in cases.values()]
    assert all(result.verifier_passed for result in results)
    assert all(result.safety_passed for result in results)


def test_invalid_action_is_rejected_and_never_executes() -> None:
    manifest = build_synthetic_manifest("replay-test")
    case = next(case for case in manifest.cases if case.kind == "COMPLETE_READ")
    result = run_replay(case, lambda _state: "erp.writer")
    assert result.status == "REJECTED"
    assert result.failure_code == "ACTION_NOT_ALLOWED"
    assert not result.safety_passed


def test_model_success_text_cannot_override_verifier() -> None:
    manifest = build_synthetic_manifest("replay-test")
    case = next(case for case in manifest.cases if case.kind == "COMPLETE_READ")
    result = run_replay(case, policy_from_actions({case.case_id: "FINISH"}))
    assert not result.verifier_passed
    assert result.failure_code == "VERIFIER_MISMATCH"


def test_tool_unknown_and_conflict_are_explicit_terminal_results() -> None:
    manifest = build_synthetic_manifest("replay-test")
    unknown = next(case for case in manifest.cases if case.kind == "TOOL_UNKNOWN")
    conflict = next(case for case in manifest.cases if case.kind == "STALE_CONFLICT")
    unknown_result = run_replay(unknown, deterministic_policy)
    conflict_result = run_replay(conflict, deterministic_policy)
    assert unknown_result.status == "UNKNOWN"
    assert conflict_result.status == "CONFLICT"
    assert unknown_result.verifier_passed and conflict_result.verifier_passed


def test_initial_policy_observation_does_not_include_case_oracle_labels() -> None:
    manifest = build_synthetic_manifest("replay-test")
    complete = next(case for case in manifest.cases if case.kind == "COMPLETE_READ")
    missing = next(case for case in manifest.cases if case.kind == "MISSING_INPUT")
    assert not hasattr(initial_state(complete), "case_id")
    assert initial_state(complete).observations == ()
    assert initial_state(complete).no_progress is False
    assert initial_state(complete).needs_input is False
    assert initial_state(complete).untrusted_content is False
    assert state_features(initial_state(complete)) == state_features(initial_state(missing))
    changed_oracle = complete.model_copy(
        update={"oracle": {"scenario": "UNTRUSTED_INJECTION"}, "expected_status": "REFUSED"}
    )
    assert state_features(initial_state(complete)) == state_features(initial_state(changed_oracle))


def test_policy_cannot_read_case_label_from_state() -> None:
    manifest = build_synthetic_manifest("replay-test")
    case = next(case for case in manifest.cases if case.kind == "COMPLETE_READ")

    def label_probe(state: object) -> str:
        return "FINISH" if not hasattr(state, "case_id") else "purchase_order.open"

    result = run_replay(case, label_probe)
    assert result.action_sequence == ("FINISH",)
    assert not result.verifier_passed


def test_candidate_content_changes_only_the_bounded_decision_preference() -> None:
    manifest = build_synthetic_manifest("replay-test")
    case = next(case for case in manifest.cases if case.kind == "COMPLETE_READ")
    ask_first = candidate_policy("Ask for missing evidence before the first read.")
    finish_when_ready = candidate_policy("Finish when evidence is sufficient.")
    assert ask_first(initial_state(case)) == "ASK_INPUT"
    assert finish_when_ready(initial_state(case)) == "purchase_order.open"
