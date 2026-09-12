"""Completion checks loaded by the strict Phase 12 stage gate."""

from __future__ import annotations

from pathlib import Path

from .contracts import CandidateVersion, DatasetManifest, ExperimentPlan, ExperimentRecord
from .stage import StageAudit


def check_completion(
    root: Path,
    output: Path,
    manifest: DatasetManifest,
    plan: ExperimentPlan,
    candidates: dict[str, CandidateVersion],
    records: tuple[ExperimentRecord, ...],
    audit: StageAudit,
) -> None:
    """Temporary completion hook; detailed artifact checks land before formal runs."""
    del root, output, manifest, plan, candidates, records
    audit.check("completion_artifacts", False)
    audit.fail("training, rollback, bootstrap and reward evidence checks are pending")


def check_review_and_harness(
    output: Path,
    plan: ExperimentPlan,
    audit: StageAudit,
    *,
    require_review: bool,
    require_harness: bool,
) -> tuple[str, str]:
    del output, plan
    if require_review:
        audit.fail("independent review is not PASS", blocked=True)
    if require_harness:
        audit.fail("Harness synchronization is not closed", blocked=True)
    return ("MISSING" if require_review else "PENDING", "MISSING" if require_harness else "PENDING")
