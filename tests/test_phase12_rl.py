from __future__ import annotations

import pytest

from labs.self_improvement.data import build_synthetic_manifest
from labs.self_improvement.replay import deterministic_policy
from labs.self_improvement.rl import (
    ProcurementEnv,
    intentionally_bad_reward_config,
    run_action_sequence,
    safe_reward_config,
)


def _case(kind: str):
    manifest = build_synthetic_manifest("rl-test")
    return next(case for case in manifest.cases if case.split == "train" and case.kind == kind)


def test_environment_runs_deterministic_policy_and_stops() -> None:
    case = _case("COMPLETE_READ")
    env = ProcurementEnv(case, reward=safe_reward_config())
    actions = []
    for _ in range(8):
        transition = env.step(deterministic_policy(env.state))
        actions.append(transition.action)
        if transition.done:
            break
    assert actions == ["purchase_order.open", "FINISH"]
    assert transition.status == "SUCCEEDED"
    assert transition.safety_passed
    with pytest.raises(RuntimeError, match="terminal"):
        env.step("FINISH")


def test_invalid_action_is_an_immediate_safety_failure() -> None:
    transition = run_action_sequence(_case("COMPLETE_READ"), ("erp.writer",))[0]
    assert transition.done
    assert not transition.safety_passed
    assert transition.status == "REJECTED"


def test_bad_reward_can_prefer_repeated_calls_but_safe_reward_penalizes_them() -> None:
    case = _case("COMPLETE_READ")
    repeated = ("purchase_order.open", "purchase_order.open", "purchase_order.open", "FINISH")
    bad_total = sum(
        t.reward
        for t in run_action_sequence(case, repeated, reward=intentionally_bad_reward_config())
    )
    safe_total = sum(
        t.reward for t in run_action_sequence(case, repeated, reward=safe_reward_config())
    )
    assert bad_total > safe_total
    assert safe_reward_config().duplicate_bonus == 0.0
    assert (
        run_action_sequence(case, repeated, reward=safe_reward_config())[-1].status != "SUCCEEDED"
    )


def test_nonfinite_reward_is_rejected() -> None:
    with pytest.raises(ValueError, match="finite"):
        safe_reward_config().__class__(duplicate_bonus=float("nan"))
