"""Completion and review checks for the Phase 12 stage gate."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .artifacts import read_records
from .candidates import read_active_version, read_selection, version_content_digest
from .contracts import (
    CandidateVersion,
    DatasetManifest,
    ExperimentPlan,
    ExperimentRecord,
    TrainingArtifact,
)
from .evaluation import grouped_bootstrap
from .stage import StageAudit

TRAINING_METHODS = ("sft", "dpo", "reinforce")
SEEDS = (17, 29, 43)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _json(path: Path) -> object:
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def _check_budget(output: Path, records: tuple[ExperimentRecord, ...], audit: StageAudit) -> None:
    live = tuple(
        record for record in records if record.experiment_id.startswith("phase12-exp-live-")
    )
    calls = sum(record.calls for record in live)
    keys = [key for record in live for key in record.reservation_keys]
    if calls > 1_200:
        audit.fail("live model call budget exceeded", blocked=True)
    if len(keys) != calls or len(set(keys)) != len(keys):
        audit.fail("live reservation keys are not one-to-one with calls", blocked=True)
    audit.count("live_calls", calls)
    audit.check("live_budget", calls <= 1_200 and len(keys) == calls == len(set(keys)))


def _check_training(
    root: Path,
    output: Path,
    manifest: DatasetManifest,
    plan: ExperimentPlan,
    audit: StageAudit,
) -> None:
    from .training import ACTION_VERSION, FEATURE_VERSION, load_weights, weights_digest

    artifacts: dict[tuple[str, int], TrainingArtifact] = {}
    try:
        for method in TRAINING_METHODS:
            for seed in SEEDS:
                weight_path = output / f"weights-{method}-{seed}.json"
                metadata_path = output / f"weights-{method}-{seed}.metadata.json"
                if (
                    not weight_path.is_file()
                    or weight_path.is_symlink()
                    or not metadata_path.is_file()
                    or metadata_path.is_symlink()
                ):
                    raise FileNotFoundError(f"missing training artifact: {method}/{seed}")
                metadata = TrainingArtifact.model_validate(_json(metadata_path))
                if (
                    metadata.method != method
                    or metadata.seed != seed
                    or metadata.code_version != plan.code_version
                    or metadata.dataset_digest != manifest.dataset_digest
                    or metadata.feature_version != FEATURE_VERSION
                    or metadata.action_version != ACTION_VERSION
                    or metadata.weight_path != str(weight_path.relative_to(root))
                ):
                    raise ValueError(f"training metadata binding mismatch: {method}/{seed}")
                model = load_weights(weight_path)
                if metadata.weight_sha256 != weights_digest(model):
                    raise ValueError(f"training weight digest mismatch: {method}/{seed}")
                if metadata.initial_weight_sha256 == metadata.weight_sha256:
                    raise ValueError(f"training did not update parameters: {method}/{seed}")
                if method == "reinforce" and (
                    not metadata.checkpoint_metrics
                    or "selected_episode" not in metadata.metrics
                    or "selected_dev_success_rate" not in metadata.metrics
                    or "selected_dev_safety_rate" not in metadata.metrics
                ):
                    raise ValueError(f"REINFORCE checkpoint selection is missing: {seed}")
                artifacts[(method, seed)] = metadata
        for seed in SEEDS:
            dpo = artifacts[("dpo", seed)]
            sft = artifacts[("sft", seed)]
            if dpo.reference_weight_sha256 != sft.weight_sha256:
                raise ValueError(f"DPO reference is not the frozen SFT weight: {seed}")
        for method in TRAINING_METHODS:
            for seed in SEEDS:
                artifact = artifacts[(method, seed)]
                for split in ("dev", "test"):
                    path = output / f"evaluation-weights-{method}-{seed}-{split}.jsonl"
                    values = read_records(root, str(path.relative_to(root)))
                    expected = {case.case_id for case in manifest.cases if case.split == split}
                    if {record.case_id for record in values} != expected or len(values) != len(
                        expected
                    ):
                        raise ValueError(
                            f"weight evaluation coverage mismatch: {method}/{seed}/{split}"
                        )
                    for record in values:
                        if (
                            record.method != f"{method}-policy"
                            or record.training_artifact_id != artifact.artifact_id
                            or record.weight_sha256 != artifact.weight_sha256
                            or record.code_version != plan.code_version
                            or record.dataset_digest != manifest.dataset_digest
                            or record.calls != 0
                            or record.case_id is None
                        ):
                            raise ValueError(
                                f"weight evaluation binding mismatch: {method}/{seed}/{split}"
                            )
        audit.count("training_artifacts", len(artifacts))
        audit.count("weight_task_evaluations", len(artifacts) * 48)
        audit.check("training_artifacts", True)
        audit.check("weight_task_evaluations", True)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        audit.check("training_artifacts", False)
        audit.check("weight_task_evaluations", False)
        audit.fail(f"training or weight evaluation invalid: {error}", blocked=True)


def _check_baseline_evaluations(
    root: Path,
    output: Path,
    manifest: DatasetManifest,
    plan: ExperimentPlan,
    audit: StageAudit,
) -> None:
    """Require initialization, seeded-random, and rule task evaluations."""
    try:
        specs: list[tuple[str, int | None, str]] = (
            [("initial", seed, split) for seed in SEEDS for split in ("dev", "test")]
            + [("random", seed, split) for seed in SEEDS for split in ("dev", "test")]
            + [("rule", None, split) for split in ("dev", "test")]
        )
        expected_artifacts: dict[int, TrainingArtifact] = {}
        for seed in SEEDS:
            metadata_path = output / f"weights-sft-{seed}.metadata.json"
            metadata = TrainingArtifact.model_validate(_json(metadata_path))
            if (
                metadata.method != "sft"
                or metadata.seed != seed
                or metadata.artifact_id != f"phase12-train-sft-seed-{seed}"
                or metadata.code_version != plan.code_version
                or metadata.dataset_digest != manifest.dataset_digest
            ):
                raise ValueError(f"initial baseline metadata binding mismatch: {seed}")
            expected_artifacts[seed] = metadata
        counts = {"initial": 0, "random": 0, "rule": 0}
        for kind, baseline_seed, split in specs:
            suffix = f"-{baseline_seed}" if baseline_seed is not None else ""
            path = output / f"evaluation-baseline-{kind}{suffix}-{split}.jsonl"
            values = read_records(root, str(path.relative_to(root)))
            expected_ids = {case.case_id for case in manifest.cases if case.split == split}
            if {record.case_id for record in values} != expected_ids or len(values) != len(
                expected_ids
            ):
                raise ValueError(
                    f"baseline evaluation coverage mismatch: {kind}/{baseline_seed}/{split}"
                )
            initial_metadata = (
                expected_artifacts.get(baseline_seed)
                if kind == "initial" and baseline_seed is not None
                else None
            )
            expected_models = {
                "initial": (
                    f"phase12-initial-baseline-seed-{baseline_seed}-"
                    f"{initial_metadata.initial_weight_sha256[:16]}"
                    if initial_metadata is not None
                    else None
                ),
                "random": f"phase12-random-baseline-seed-{baseline_seed}",
                "rule": "phase12-rule-baseline",
            }
            for record in values:
                if (
                    record.method != f"{kind}-policy"
                    or record.model != expected_models[kind]
                    or record.split != split
                    or record.dataset_id != manifest.dataset_id
                    or record.code_version != plan.code_version
                    or record.dataset_digest != manifest.dataset_digest
                    or record.calls != 0
                    or record.repeat != 1
                    or record.candidate_id is not None
                    or record.status not in {"SUCCEEDED", "REJECTED"}
                ):
                    raise ValueError(
                        f"baseline evaluation binding mismatch: {kind}/{baseline_seed}/{split}"
                    )
                if kind == "initial":
                    if (
                        initial_metadata is None
                        or record.training_artifact_id != initial_metadata.artifact_id
                        or record.weight_sha256 != initial_metadata.initial_weight_sha256
                    ):
                        raise ValueError(
                            f"initial baseline weight binding mismatch: {baseline_seed}"
                        )
                elif record.training_artifact_id is not None or record.weight_sha256 is not None:
                    raise ValueError(f"non-weight baseline unexpectedly binds a weight: {kind}")
            counts[kind] += len(values)
        audit.count("initial_task_evaluations", counts["initial"])
        audit.count("random_task_evaluations", counts["random"])
        audit.count("rule_task_evaluations", counts["rule"])
        audit.count("baseline_task_evaluations", sum(counts.values()))
        audit.check("baseline_task_evaluations", True)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        audit.check("baseline_task_evaluations", False)
        audit.fail(f"baseline task evaluations invalid: {error}", blocked=True)


def _check_selections(
    output: Path, records: tuple[ExperimentRecord, ...], audit: StageAudit
) -> None:
    try:
        directory = output / "selections"
        paths = tuple(sorted(directory.glob("*.json"))) if directory.is_dir() else ()
        if len(paths) < 2:
            raise ValueError("at least one SELECT and one ROLLBACK receipt are required")
        evidence = {record.experiment_id for record in records}
        actions: list[str] = []
        for path in paths:
            selection = read_selection(output, path.stem)
            actions.append(selection.action)
            if not set(selection.evidence_ids).issubset(evidence):
                raise ValueError(f"selection evidence is missing: {selection.selection_id}")
            for version_id, digest in selection.version_digests.items():
                if version_content_digest(output, version_id) != digest:
                    raise ValueError(f"selection digest mismatch: {version_id}")
        active = read_active_version(output)
        if (
            active is None
            or version_content_digest(output, active.version_id) != active.content_sha256
        ):
            raise ValueError("active lab pointer is missing or stale")
        if "SELECT" not in actions or "ROLLBACK" not in actions:
            raise ValueError("selection history must include SELECT and ROLLBACK")
        audit.count("selections", len(paths))
        audit.check("selection_and_rollback", True)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        audit.check("selection_and_rollback", False)
        audit.fail(f"selection or rollback evidence invalid: {error}", blocked=True)


def _check_rollback(
    output: Path, manifest: DatasetManifest, plan: ExperimentPlan, audit: StageAudit
) -> None:
    try:
        payload = _json(output / "phase12-rollback-evidence.json")
        required = {
            "schema_version",
            "plan_id",
            "dataset_digest",
            "case_ids",
            "before_digest",
            "after_digest",
            "selected_digest",
            "behavior_restored",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) != required
            or payload["schema_version"] != "1"
        ):
            raise ValueError("rollback evidence shape is invalid")
        if (
            payload["plan_id"] != plan.plan_id
            or payload["dataset_digest"] != manifest.dataset_digest
        ):
            raise ValueError("rollback evidence binding mismatch")
        cases = payload["case_ids"]
        dev_ids = {case.case_id for case in manifest.cases if case.split == "dev"}
        if not isinstance(cases, list) or not cases or not set(cases).issubset(dev_ids):
            raise ValueError("rollback evidence cases are invalid")
        digests = [payload[name] for name in ("before_digest", "after_digest", "selected_digest")]
        if any(not isinstance(value, str) or not _DIGEST.fullmatch(value) for value in digests):
            raise ValueError("rollback evidence digest is invalid")
        if (
            payload["before_digest"] != payload["after_digest"]
            or payload["behavior_restored"] is not True
        ):
            raise ValueError("rollback did not restore exact behavior")
        audit.check("rollback_behavior_restored", True)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        audit.check("rollback_behavior_restored", False)
        audit.fail(f"rollback replay evidence invalid: {error}", blocked=True)


def _check_bootstrap(
    output: Path,
    manifest: DatasetManifest,
    plan: ExperimentPlan,
    records: tuple[ExperimentRecord, ...],
    test_methods: tuple[str, ...],
    audit: StageAudit,
) -> None:
    try:
        payload = _json(
            output / f"phase12-bootstrap-{plan.plan_id.removeprefix('phase12-plan-')}.json"
        )
        required = {"schema_version", "plan_id", "dataset_digest", "code_version", "comparisons"}
        if (
            not isinstance(payload, dict)
            or set(payload) != required
            or payload["schema_version"] != "1"
        ):
            raise ValueError("bootstrap evidence shape is invalid")
        if (
            payload["plan_id"] != plan.plan_id
            or payload["dataset_digest"] != manifest.dataset_digest
            or payload["code_version"] != plan.code_version
        ):
            raise ValueError("bootstrap evidence binding mismatch")
        expected = set(test_methods) - {"baseline"}
        comparisons = payload["comparisons"]
        fields = {
            "method_a",
            "method_b",
            "groups",
            "delta",
            "lower_95",
            "upper_95",
            "conclusion",
            "samples",
        }
        if not isinstance(comparisons, list) or len(comparisons) != len(expected):
            raise ValueError("bootstrap comparison count is incomplete")
        seen: set[str] = set()
        for comparison in comparisons:
            if not isinstance(comparison, dict) or set(comparison) != fields:
                raise ValueError("bootstrap comparison shape is invalid")
            method_a = comparison["method_a"]
            if (
                comparison["method_b"] != "baseline"
                or not isinstance(method_a, str)
                or method_a not in expected
                or method_a in seen
                or comparison["groups"] != 12
                or comparison["samples"] != 2_000
                or comparison["conclusion"] not in {"IMPROVED", "INCONCLUSIVE", "REGRESSED"}
            ):
                raise ValueError("bootstrap comparison contract is invalid")
            seen.add(method_a)
        if seen != expected:
            raise ValueError("bootstrap comparisons do not cover frozen methods")
        live = tuple(
            record
            for record in records
            if (
                record.experiment_id.startswith("phase12-exp-live-")
                and record.experiment_plan_id == plan.plan_id
                and record.split == "test"
            )
        )
        baseline = tuple(record for record in live if record.method == "baseline")
        expected_by_method = {comparison["method_a"]: comparison for comparison in comparisons}
        for method in sorted(expected):
            candidate = tuple(record for record in live if record.method == method)
            calculated = grouped_bootstrap(
                candidate,
                baseline,
                method_a=method,
                method_b="baseline",
            )
            actual = expected_by_method[method]
            for field in ("method_a", "method_b", "groups", "conclusion"):
                if actual[field] != getattr(calculated, field):
                    raise ValueError(f"bootstrap calculation mismatch for {method}: {field}")
            if actual["samples"] != 2_000:
                raise ValueError(f"bootstrap calculation mismatch for {method}: samples")
            for field in ("delta", "lower_95", "upper_95"):
                value = actual[field]
                expected_value = getattr(calculated, field)
                if not isinstance(value, (int, float)) or not math.isclose(
                    float(value), expected_value, rel_tol=1e-12, abs_tol=1e-12
                ):
                    raise ValueError(f"bootstrap calculation mismatch for {method}: {field}")
        audit.count("bootstrap_comparisons", len(comparisons))
        audit.check("heldout_bootstrap", True)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        audit.check("heldout_bootstrap", False)
        audit.fail(f"held-out bootstrap invalid: {error}", blocked=True)


def _check_reward(output: Path, manifest: DatasetManifest, audit: StageAudit) -> None:
    try:
        payload = _json(output / "phase12-reward-hacking.json")
        train_ids = {case.case_id for case in manifest.cases if case.split == "train"}
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != "1"
            or payload.get("case_id") not in train_ids
        ):
            raise ValueError("reward evidence shape is invalid")
        if (
            payload.get("is_prespecified_negative") is not True
            or payload.get("bad_reward_higher") is not True
            or payload.get("task_verifier_passed") is not False
        ):
            raise ValueError("reward evidence does not show the prespecified negative")
        audit.check("reward_hacking_negative", True)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        audit.check("reward_hacking_negative", False)
        audit.fail(f"reward-hacking evidence invalid: {error}", blocked=True)


def check_completion(
    root: Path,
    output: Path,
    manifest: DatasetManifest,
    plan: ExperimentPlan,
    candidates: dict[str, CandidateVersion],
    records: tuple[ExperimentRecord, ...],
    audit: StageAudit,
    *,
    test_methods: tuple[str, ...],
) -> None:
    del candidates
    _check_budget(output, records, audit)
    _check_training(root, output, manifest, plan, audit)
    _check_baseline_evaluations(root, output, manifest, plan, audit)
    _check_selections(output, records, audit)
    _check_rollback(output, manifest, plan, audit)
    _check_bootstrap(output, manifest, plan, records, test_methods, audit)
    _check_reward(output, manifest, audit)


def check_review_and_harness(
    output: Path,
    plan: ExperimentPlan,
    audit: StageAudit,
    *,
    require_review: bool,
    require_harness: bool,
) -> tuple[str, str]:
    review_status = "PENDING"
    harness_status = "PENDING"
    if require_review:
        try:
            payload = _json(output / "phase12-review-final.json")
            if (
                not isinstance(payload, dict)
                or payload.get("status") != "PASS"
                or payload.get("plan_id") != plan.plan_id
            ):
                raise ValueError("independent review is not PASS")
            review_status = "PASS"
            audit.check("independent_review", True)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
            audit.fail(f"independent review is not PASS: {error}", blocked=True)
            review_status = "MISSING"
    elif (output / "phase12-review-final.json").is_file():
        review_status = "PRESENT"
    if require_harness:
        try:
            payload = _json(output / "phase12-harness-sync.json")
            if (
                not isinstance(payload, dict)
                or payload.get("status") not in {"APPROVED", "NO_CHANGE"}
                or payload.get("plan_id") != plan.plan_id
            ):
                raise ValueError("Harness synchronization is not closed")
            harness_status = str(payload["status"])
            audit.check("harness_sync", True)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
            audit.fail(f"Harness synchronization is not closed: {error}", blocked=True)
            harness_status = "MISSING"
    elif (output / "phase12-harness-sync.json").is_file():
        harness_status = "PRESENT"
    return review_status, harness_status
