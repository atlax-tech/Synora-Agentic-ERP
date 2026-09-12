"""Independent task evaluation for local Phase 12 policy weights."""

from __future__ import annotations

from pathlib import Path

from .contracts import DatasetManifest, ExperimentRecord
from .replay import run_replay
from .training import load_weights, policy_for_model, weights_digest


def evaluate_weight_artifact(
    manifest: DatasetManifest,
    weight_path: Path,
    *,
    method: str,
    seed: int,
    split: str,
    code_version: str,
    artifact_id: str,
) -> tuple[ExperimentRecord, ...]:
    """Load JSON weights in-process and evaluate every case with no gradients."""
    model = load_weights(weight_path)
    weight_sha256 = weights_digest(model)
    policy = policy_for_model(model)
    records: list[ExperimentRecord] = []
    for case in manifest.cases:
        if case.split != split:
            continue
        result = run_replay(case, policy)
        records.append(
            ExperimentRecord(
                experiment_id=(f"phase12-exp-weight-{method}-seed-{seed}-{split}-{case.case_id}"),
                code_version=code_version,
                dataset_id=manifest.dataset_id,
                dataset_digest=manifest.dataset_digest,
                split=case.split,
                case_id=case.case_id,
                group_id=case.group_id,
                method=f"{method}-policy",
                training_artifact_id=artifact_id,
                weight_sha256=weight_sha256,
                model=f"phase12-policy-{method}-seed-{seed}-{weight_sha256[:16]}",
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
        )
    if not records:
        raise ValueError(f"no {split} cases are available")
    return tuple(records)
