"""Independent local task baselines for the Phase 12 held-out environment."""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Literal

from .contracts import DatasetManifest, ExperimentRecord
from .replay import ALL_ACTIONS, ReplayResult, deterministic_policy, run_replay
from .training import _new_model, policy_for_model, weights_digest


def _record(
    manifest: DatasetManifest,
    case_id: str,
    result: ReplayResult,
    *,
    kind: str,
    seed: int | None,
    split: Literal["dev", "test"],
    code_version: str,
    model: str,
    training_artifact_id: str | None = None,
    weight_sha256: str | None = None,
) -> ExperimentRecord:
    seed_suffix = f"-seed-{seed}" if seed is not None else ""
    return ExperimentRecord(
        experiment_id=f"phase12-exp-baseline-{kind}{seed_suffix}-{split}-{case_id}",
        code_version=code_version,
        dataset_id=manifest.dataset_id,
        dataset_digest=manifest.dataset_digest,
        split=split,
        case_id=case_id,
        group_id=next(case.group_id for case in manifest.cases if case.case_id == case_id),
        method=f"{kind}-policy",
        training_artifact_id=training_artifact_id,
        weight_sha256=weight_sha256,
        model=model,
        repeat=1,
        status="SUCCEEDED" if result.verifier_passed else "REJECTED",
        output_action=result.action_sequence[-1] if result.action_sequence else None,
        verifier_passed=result.verifier_passed,
        safety_passed=result.safety_passed,
        score=result.score,
        failure_code=None if result.verifier_passed else result.failure_code,
        elapsed_ms=0.0,
        calls=0,
    )


def _evaluate(
    manifest: DatasetManifest,
    *,
    split: Literal["dev", "test"],
    kind: str,
    seed: int | None,
    policy: Callable[..., str],
    code_version: str,
    model: str,
    training_artifact_id: str | None = None,
    weight_sha256: str | None = None,
) -> tuple[ExperimentRecord, ...]:
    records = []
    for case in manifest.cases:
        if case.split != split:
            continue
        result = run_replay(case, policy)
        records.append(
            _record(
                manifest,
                case.case_id,
                result,
                kind=kind,
                seed=seed,
                split=split,
                code_version=code_version,
                model=model,
                training_artifact_id=training_artifact_id,
                weight_sha256=weight_sha256,
            )
        )
    if not records:
        raise ValueError(f"no {split} cases are available")
    return tuple(records)


def random_policy(seed: int) -> Callable[..., str]:
    """Return a seeded random policy that never reads oracle or case labels."""
    rng = random.Random(seed)

    def choose(_state: object) -> str:
        return rng.choice(ALL_ACTIONS)

    return choose


def evaluate_random_baseline(
    manifest: DatasetManifest,
    *,
    seed: int,
    split: Literal["dev", "test"],
    code_version: str,
) -> tuple[ExperimentRecord, ...]:
    return _evaluate(
        manifest,
        split=split,
        kind="random",
        seed=seed,
        policy=random_policy(seed),
        code_version=code_version,
        model=f"phase12-random-baseline-seed-{seed}",
    )


def evaluate_rule_baseline(
    manifest: DatasetManifest,
    *,
    split: Literal["dev", "test"],
    code_version: str,
) -> tuple[ExperimentRecord, ...]:
    return _evaluate(
        manifest,
        split=split,
        kind="rule",
        seed=None,
        policy=deterministic_policy,
        code_version=code_version,
        model="phase12-rule-baseline",
    )


def evaluate_initial_baseline(
    manifest: DatasetManifest,
    *,
    seed: int,
    split: Literal["dev", "test"],
    code_version: str,
    training_artifact_id: str,
    expected_weight_sha256: str,
) -> tuple[ExperimentRecord, ...]:
    """Evaluate the reproducible initialization used by the matching SFT seed."""
    model = _new_model(seed)
    weight_sha256 = weights_digest(model)
    if weight_sha256 != expected_weight_sha256:
        raise ValueError(f"initial weight digest mismatch for seed {seed}")
    return _evaluate(
        manifest,
        split=split,
        kind="initial",
        seed=seed,
        policy=policy_for_model(model),
        code_version=code_version,
        model=f"phase12-initial-baseline-seed-{seed}-{weight_sha256[:16]}",
        training_artifact_id=training_artifact_id,
        weight_sha256=weight_sha256,
    )
