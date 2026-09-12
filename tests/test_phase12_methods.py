from __future__ import annotations

from labs.self_improvement.contracts import DatasetCase
from labs.self_improvement.data import build_synthetic_manifest
from labs.self_improvement.evaluation import best_of_n_replay, reflection_replay, rerank_candidates
from labs.self_improvement.replay import deterministic_policy, policy_from_actions


def _case(kind: str) -> DatasetCase:
    manifest = build_synthetic_manifest("methods-test")
    return next(case for case in manifest.cases if case.split == "dev" and case.kind == kind)


def test_reflection_can_repair_a_failed_first_attempt_once() -> None:
    case = _case("COMPLETE_READ")
    first = policy_from_actions({case.case_id: "FINISH"})
    outcome = reflection_replay(case, first, deterministic_policy)
    assert outcome.calls == 2
    assert outcome.improved
    assert outcome.result.verifier_passed


def test_reflection_does_not_add_a_call_to_a_verified_result() -> None:
    case = _case("MISSING_INPUT")
    outcome = reflection_replay(case, deterministic_policy, lambda _state: "FINISH")
    assert outcome.calls == 1
    assert not outcome.improved


def test_best_of_n_rejects_unverified_candidates_before_sorting() -> None:
    case = _case("MISSING_INPUT")
    bad = policy_from_actions({case.case_id: "purchase_order.open"})
    good = deterministic_policy
    outcome = best_of_n_replay(case, (bad, good), n=2)
    assert outcome.calls == 2
    assert outcome.result.verifier_passed


def test_reranker_has_no_accepted_candidate_for_unsafe_or_wrong_actions() -> None:
    case = _case("MISSING_INPUT")
    assert rerank_candidates(case, ("erp.writer", "FINISH")) == ()
