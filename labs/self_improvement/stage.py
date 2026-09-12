"""Strict, read-only checks for the Phase 12 exit evidence graph."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .artifacts import (
    PHASE12_RELATIVE_ROOT,
    code_version_is_compatible,
    read_json,
    read_manifest,
    read_records,
    verify_manifest,
    verify_records,
)
from .candidates import lab_boundary_digest, read_candidate
from .contracts import (
    CandidateVersion,
    DatasetCase,
    DatasetManifest,
    ExperimentPlan,
    ExperimentRecord,
)
from .data import audit_historical_failures, model_input_text
from .evaluation import ReservationLedger

StageStatus = Literal["INCOMPLETE", "BLOCKED", "PASS"]
LIVE_METHODS = ("baseline", "reflection", "best-of-3", "prompt-candidate", "skill-candidate")
TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "UNKNOWN", "REJECTED"})


@dataclass(frozen=True)
class StageVerification:
    status: StageStatus
    reasons: tuple[str, ...]
    checks: dict[str, bool]
    counts: dict[str, int]
    plan_id: str | None = None
    dataset_digest: str | None = None
    review_status: str = "MISSING"
    harness_status: str = "MISSING"

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "1",
            "status": self.status,
            "reasons": list(self.reasons),
            "checks": dict(sorted(self.checks.items())),
            "counts": dict(sorted(self.counts.items())),
            "plan_id": self.plan_id,
            "dataset_digest": self.dataset_digest,
            "review_status": self.review_status,
            "harness_status": self.harness_status,
        }


class StageAudit:
    def __init__(self) -> None:
        self.reasons: list[str] = []
        self.checks: dict[str, bool] = {}
        self.counts: dict[str, int] = {}
        self.blocked = False
        self.incomplete = False

    def check(self, name: str, passed: bool) -> None:
        self.checks[name] = passed

    def fail(self, reason: str, *, blocked: bool = False) -> None:
        self.reasons.append(reason)
        self.blocked |= blocked
        self.incomplete |= not blocked

    def count(self, name: str, value: int) -> None:
        self.counts[name] = value


def _output(root: Path) -> Path:
    output = (root / PHASE12_RELATIVE_ROOT).resolve()
    if not output.is_dir() or output.is_symlink():
        raise FileNotFoundError("Phase 12 output root is missing")
    return output


def _audit_payload(root: Path) -> tuple[dict[str, object], ...]:
    payload = read_json(root, f"{PHASE12_RELATIVE_ROOT}/audited-failures.json")
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("audited failure artifact has an invalid shape")
    values = tuple(payload["records"])
    if any(not isinstance(value, dict) for value in values):
        raise ValueError("audited failure records must be objects")
    return tuple(value for value in values if isinstance(value, dict))


def _check_audit(root: Path, audit: StageAudit) -> set[str]:
    try:
        expected = audit_historical_failures(root)
        if _audit_payload(root) != tuple(item.model_dump(mode="json") for item in expected):
            raise ValueError("audited failure artifact does not match allowlisted sources")
        if not any(item.review_status == "BACKGROUND_ONLY" for item in expected):
            raise ValueError("no traceable historical failure is available")
        audit.count("audited_records", len(expected))
        audit.check("historical_audit_matches_allowlist", True)
        audit.check("historical_failure_motivation", True)
        return {item.case_id for item in expected}
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        audit.check("historical_audit_matches_allowlist", False)
        audit.fail(f"historical audit invalid: {error}", blocked=True)
        return set()


def _check_dataset(root: Path, audit: StageAudit) -> DatasetManifest | None:
    try:
        manifest = read_manifest(root, f"{PHASE12_RELATIVE_ROOT}/dataset-phase12-synthetic-v2.json")
        verify_manifest(manifest)
        projections = tuple(model_input_text(case) for case in manifest.cases)
        if len(set(projections)) != len(projections):
            raise ValueError("model-visible case projections are not unique")
        for case, projection in zip(manifest.cases, projections, strict=True):
            if any(
                label in projection
                for label in (case.kind, case.expected_action, case.expected_status)
            ):
                raise ValueError(f"scoring label leaked into model input: {case.case_id}")
            if any(marker in projection.casefold() for marker in ("oracle=", "case_id=", "split=")):
                raise ValueError(f"metadata leaked into model input: {case.case_id}")
        audit.count("dataset_cases", manifest.case_count)
        audit.count("dataset_groups", sum(manifest.group_counts.values()))
        audit.check("dataset_manifest", True)
        audit.check("observable_inputs", True)
        return manifest
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        audit.check("dataset_manifest", False)
        audit.check("observable_inputs", False)
        audit.fail(f"dataset invalid: {error}", blocked=True)
        return None


def _check_candidates(
    output: Path, reviewed_ids: set[str], audit: StageAudit
) -> dict[str, CandidateVersion]:
    candidates: dict[str, CandidateVersion] = {}
    try:
        directory = output / "candidates"
        if not directory.is_dir() or directory.is_symlink():
            raise FileNotFoundError("candidate directory is missing")
        paths = tuple(sorted(directory.glob("*.json")))
        if any(path.is_symlink() or not path.is_file() for path in paths):
            raise ValueError("candidate artifact must be a regular file")
        for path in paths:
            candidate = read_candidate(output, path.stem)
            if not set(candidate.source_case_ids).issubset(reviewed_ids):
                raise ValueError(f"candidate has unaudited source: {candidate.candidate_id}")
            if candidate.boundary_sha256 != lab_boundary_digest():
                raise ValueError(f"candidate boundary is stale: {candidate.candidate_id}")
            candidates[candidate.candidate_id] = candidate
        counts = {
            kind: sum(item.kind == kind for item in candidates.values())
            for kind in ("PROMPT", "SKILL")
        }
        if not 1 <= counts["PROMPT"] <= 2 or not 1 <= counts["SKILL"] <= 2:
            raise ValueError("one or two Prompt and Skill candidates are required")
        audit.count("candidates", len(candidates))
        audit.count("prompt_candidates", counts["PROMPT"])
        audit.count("skill_candidates", counts["SKILL"])
        audit.check("candidate_boundaries", True)
        return candidates
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        audit.check("candidate_boundaries", False)
        audit.fail(f"candidate artifacts invalid: {error}", blocked=True)
        return {}


def _all_records(root: Path, output: Path, audit: StageAudit) -> tuple[ExperimentRecord, ...]:
    try:
        paths = tuple(
            sorted(
                path for path in output.glob("*.jsonl") if path.name != "live-reservations.jsonl"
            )
        )
        if any(path.is_symlink() or not path.is_file() for path in paths):
            raise ValueError("record file is not regular")
        records = tuple(
            record for path in paths for record in read_records(root, str(path.relative_to(root)))
        )
        audit.count("record_files", len(paths))
        audit.count("records", len(records))
        return records
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        audit.fail(f"experiment records cannot be read: {error}", blocked=True)
        return ()


def _case_id(record: ExperimentRecord, cases: dict[str, DatasetCase]) -> str | None:
    if record.case_id is not None:
        return record.case_id if record.case_id in cases else None
    matches = [case_id for case_id in cases if record.experiment_id.endswith("-" + case_id)]
    return max(matches, key=len) if matches else None


def _expected(
    manifest: DatasetManifest, methods: tuple[str, ...], split: str
) -> set[tuple[str, int, str]]:
    return {
        (method, repeat, case.case_id)
        for method in methods
        for repeat in range(1, 4)
        for case in manifest.cases
        if case.split == split
    }


def _check_live_matrix(
    manifest: DatasetManifest,
    plan: ExperimentPlan,
    records: tuple[ExperimentRecord, ...],
    candidates: dict[str, CandidateVersion],
    split: Literal["dev", "test"],
    audit: StageAudit,
) -> set[str]:
    methods = tuple(plan.dev_methods if split == "dev" else plan.test_methods)
    expected = _expected(manifest, methods, split)
    cases = {case.case_id: case for case in manifest.cases if case.split == split}
    live = tuple(
        record
        for record in records
        if record.experiment_id.startswith("phase12-exp-live-")
        and record.experiment_plan_id == plan.plan_id
        and record.split == split
    )
    actual: dict[tuple[str, int, str], ExperimentRecord] = {}
    for record in records:
        if (
            record.experiment_id.startswith("phase12-exp-live-")
            and record.experiment_plan_id != plan.plan_id
        ):
            audit.fail(
                f"live record is bound to another or missing plan: {record.experiment_id}",
                blocked=True,
            )
    for record in live:
        case_id = _case_id(record, cases)
        key = (record.method, record.repeat, case_id or "")
        if key in actual:
            audit.fail(f"duplicate live trial: {record.experiment_id}", blocked=True)
        actual[key] = record
        if key not in expected:
            audit.fail(f"unexpected live trial: {record.experiment_id}", blocked=True)
        if (
            record.code_version != plan.code_version
            or record.model != plan.model
            or record.dataset_digest != manifest.dataset_digest
            or case_id is None
        ):
            audit.fail(f"live trial binding mismatch: {record.experiment_id}", blocked=True)
        if record.status not in TERMINAL_STATUSES:
            audit.fail(f"live trial is not terminal: {record.experiment_id}", blocked=True)
        if record.calls < 1 or len(record.reservation_keys) != record.calls:
            audit.fail(
                f"live trial reservation count mismatch: {record.experiment_id}", blocked=True
            )
        if len(set(record.reservation_keys)) != record.calls:
            audit.fail(
                f"live trial has invalid reservation keys: {record.experiment_id}", blocked=True
            )
        if (
            record.method in {"baseline", "prompt-candidate", "skill-candidate"}
            and record.calls != 1
        ):
            audit.fail(
                f"single-call method has unexpected calls: {record.experiment_id}", blocked=True
            )
        if record.method == "reflection" and record.calls not in {1, 2}:
            audit.fail(f"reflection call bound exceeded: {record.experiment_id}", blocked=True)
        if record.method == "best-of-3" and record.calls != 3:
            audit.fail(
                f"Best-of-3 did not consume three calls: {record.experiment_id}", blocked=True
            )
        if record.method in {"prompt-candidate", "skill-candidate"}:
            candidate = candidates.get(record.candidate_id or "")
            if candidate is None or (candidate.kind == "PROMPT") != (
                record.method == "prompt-candidate"
            ):
                audit.fail(
                    f"live candidate binding is invalid: {record.experiment_id}", blocked=True
                )
            elif (
                record.candidate_content_sha256 != candidate.content_sha256
                or record.candidate_boundary_sha256 != candidate.boundary_sha256
            ):
                audit.fail(f"live candidate digest mismatch: {record.experiment_id}", blocked=True)
        elif record.candidate_id is not None:
            audit.fail(
                f"non-candidate live method has candidate binding: {record.experiment_id}",
                blocked=True,
            )
    missing = expected - set(actual)
    if missing:
        audit.fail(f"{split} live matrix is incomplete; missing {len(missing)} trials")
    audit.count(f"live_{split}_records", len(live))
    audit.count(f"live_{split}_expected", len(expected))
    audit.check(f"live_{split}_matrix", not missing)
    return {key for record in live for key in record.reservation_keys}


def _check_ledger(output: Path, expected_keys: set[str], audit: StageAudit) -> None:
    try:
        path = output / "live-reservations.jsonl"
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError("reservation ledger is missing")
        states = dict(ReservationLedger(path).states())
        if any(state != "RECORDED" for state in states.values()) or set(states) != expected_keys:
            raise ValueError("reservation ledger does not exactly match live records")
        audit.count("reserved_calls", len(states))
        audit.check("reservation_ledger", True)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        audit.check("reservation_ledger", False)
        audit.fail(f"reservation ledger invalid: {error}", blocked=True)


def verify_stage(
    root: Path, *, require_review: bool = True, require_harness: bool = True
) -> StageVerification:
    """Check all evidence and return a computed status; never writes artifacts."""
    audit = StageAudit()
    try:
        output = _output(root)
    except FileNotFoundError as error:
        return StageVerification("INCOMPLETE", (str(error),), {}, {}, None, None)
    manifest = _check_dataset(root, audit)
    reviewed_ids = _check_audit(root, audit)
    candidates = _check_candidates(output, reviewed_ids, audit)
    plan: ExperimentPlan | None = None
    try:
        path = output / "phase12-experiment-manifest.json"
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError("experiment plan is missing")
        plan = ExperimentPlan.model_validate_json(path.read_text(encoding="utf-8"))
        if (
            manifest is None
            or plan.dataset_id != manifest.dataset_id
            or plan.dataset_digest != manifest.dataset_digest
        ):
            raise ValueError("experiment plan is bound to another dataset")
        if not set(plan.candidate_ids).issubset(candidates):
            raise ValueError("experiment plan references a missing candidate")
        if not code_version_is_compatible(plan.code_version):
            raise ValueError("experiment plan is bound to changed implementation code")
        audit.check("experiment_plan", True)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        audit.check("experiment_plan", False)
        audit.fail(f"experiment plan invalid: {error}")
    records = _all_records(root, output, audit)
    expected_keys: set[str] = set()
    if manifest is not None and plan is not None:
        try:
            verify_records(records, manifest)
        except ValueError as error:
            audit.fail(f"experiment records fail the base contract: {error}", blocked=True)
        expected_keys |= _check_live_matrix(manifest, plan, records, candidates, "dev", audit)
        expected_keys |= _check_live_matrix(manifest, plan, records, candidates, "test", audit)
        _check_ledger(output, expected_keys, audit)
        from .stage_completion import check_completion

        check_completion(root, output, manifest, plan, candidates, records, audit)
    else:
        audit.fail("strict matrix checks cannot run without a valid dataset and experiment plan")
    review_status = "PENDING" if not require_review else "MISSING"
    harness_status = "PENDING" if not require_harness else "MISSING"
    if plan is not None and (require_review or require_harness):
        from .stage_completion import check_review_and_harness

        review_status, harness_status = check_review_and_harness(
            output, plan, audit, require_review=require_review, require_harness=require_harness
        )
    status: StageStatus = (
        "BLOCKED" if audit.blocked else "INCOMPLETE" if audit.incomplete else "PASS"
    )
    return StageVerification(
        status,
        tuple(audit.reasons),
        audit.checks,
        audit.counts,
        plan.plan_id if plan else None,
        manifest.dataset_digest if manifest else None,
        review_status,
        harness_status,
    )
