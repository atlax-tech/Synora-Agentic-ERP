from __future__ import annotations

import pytest

from labs.self_improvement.data import build_synthetic_manifest
from labs.self_improvement.evaluation import grouped_bootstrap, held_out_replay
from labs.self_improvement.replay import deterministic_policy, policy_from_actions


def test_held_out_replay_is_frozen_to_test_cases_and_keeps_failures() -> None:
    manifest = build_synthetic_manifest("heldout-test")
    bad = policy_from_actions({})
    records = held_out_replay(
        manifest,
        {"baseline": deterministic_policy, "bad": bad},
        repeats=1,
        code_version="heldout-test",
    )
    assert len(records) == 48
    assert {record.split for record in records} == {"test"}
    assert any(not record.verifier_passed for record in records)


def test_grouped_bootstrap_reports_inconclusive_when_delta_is_zero() -> None:
    manifest = build_synthetic_manifest("heldout-test")
    records = held_out_replay(manifest, {"a": deterministic_policy}, code_version="heldout-test")
    summary = grouped_bootstrap(records, records, method_a="a", method_b="a")
    assert summary.groups == 12
    assert summary.delta == 0.0
    assert summary.conclusion == "INCONCLUSIVE"


def test_grouped_bootstrap_rejects_empty_or_tiny_samples() -> None:
    with pytest.raises(ValueError, match="at least 100"):
        grouped_bootstrap((), (), method_a="a", method_b="b", samples=10)
