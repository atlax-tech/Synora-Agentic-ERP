"""Pure review and correction decisions for governed Memory records.

The functions in this module rebuild validated immutable records and never
write storage.  A correction result is a pair that a later persistence layer
must commit atomically; this module does not provide a transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from pydantic import ValidationError

from agent_runtime.memory.contracts import MemoryRecord
from agent_runtime.memory.state import MemoryStateError, transition_state


def _decision_error(code: str, message: str) -> MemoryStateError:
    return MemoryStateError(code, message)


def _review_context(
    reviewer: object,
    reviewed_at: object,
    now: object,
) -> tuple[str, datetime, datetime]:
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise _decision_error("INVALID_REVIEW", "reviewer must be non-blank text")
    reviewed = _aware_timestamp(reviewed_at, "reviewed_at")
    decision_now = _aware_timestamp(now, "now")
    if reviewed > decision_now:
        raise _decision_error("INVALID_REVIEW", "reviewed_at cannot be in the future")
    return reviewer, reviewed, decision_now


def _aware_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _decision_error("INVALID_TIMESTAMP", f"{field_name} must be timezone-aware")
    return value


def _ensure_review_chronology(record: MemoryRecord, reviewed_at: datetime) -> None:
    if reviewed_at < record.created_at:
        raise _decision_error("INVALID_TIMESTAMP", "reviewed_at must not precede created_at")
    if record.expires_at is not None and reviewed_at >= record.expires_at:
        raise _decision_error("EXPIRED", "review must occur before memory expiry")


def _ensure_not_expired(record: MemoryRecord, now: datetime, label: str) -> None:
    if record.expires_at is not None and now >= record.expires_at:
        raise _decision_error("EXPIRED", f"{label} memory is expired")


def _rebuild(record: MemoryRecord, updates: Mapping[str, object]) -> MemoryRecord:
    """Reconstruct through Pydantic so updates cannot bypass invariants."""

    values = dict(record.model_dump())
    values.update(updates)
    try:
        return MemoryRecord(**values)
    except ValidationError as exc:
        raise _decision_error("INVALID_DECISION", "resulting memory record is invalid") from exc


def approve_candidate(
    candidate: MemoryRecord,
    *,
    reviewer: str,
    reviewed_at: datetime,
    now: datetime,
    expected_version: int,
) -> MemoryRecord:
    """Approve one unexpired candidate without mutating its input record."""

    reviewer_value, reviewed, decision_now = _review_context(reviewer, reviewed_at, now)
    if candidate.kind == "WORKING":
        raise _decision_error("INVALID_DECISION", "working memory cannot be durably approved")
    if candidate.kind == "EPISODIC" and candidate.expires_at is None:
        raise _decision_error("INVALID_DECISION", "episodic approval requires an explicit expiry")
    _ensure_review_chronology(candidate, reviewed)
    _ensure_not_expired(candidate, decision_now, "candidate")
    new_state, new_version = transition_state(
        candidate.state,
        "APPROVED",
        state_version=candidate.state_version,
        expected_version=expected_version,
    )
    return _rebuild(
        candidate,
        {
            "state": new_state,
            "state_version": new_version,
            "reviewer": reviewer_value,
            "reviewed_at": reviewed,
        },
    )


def reject_candidate(
    candidate: MemoryRecord,
    *,
    reviewer: str,
    reviewed_at: datetime,
    now: datetime,
    expected_version: int,
    review_reason: str | None = None,
) -> MemoryRecord:
    """Reject one candidate with optional bounded review rationale."""

    reviewer_value, reviewed, decision_now = _review_context(reviewer, reviewed_at, now)
    if candidate.kind == "WORKING":
        raise _decision_error("INVALID_DECISION", "working memory cannot be durably rejected")
    _ensure_review_chronology(candidate, reviewed)
    _ensure_not_expired(candidate, decision_now, "candidate")
    new_state, new_version = transition_state(
        candidate.state,
        "REJECTED",
        state_version=candidate.state_version,
        expected_version=expected_version,
    )
    return _rebuild(
        candidate,
        {
            "state": new_state,
            "state_version": new_version,
            "reviewer": reviewer_value,
            "reviewed_at": reviewed,
            "review_reason": review_reason,
        },
    )


def approve_correction(
    candidate: MemoryRecord,
    prior: MemoryRecord,
    *,
    reviewer: str,
    reviewed_at: datetime,
    now: datetime,
    expected_candidate_version: int,
    expected_prior_version: int,
) -> tuple[MemoryRecord, MemoryRecord]:
    """Approve a correction and supersede its exact approved predecessor.

    The returned ``(correction, prior)`` pair is not persisted here.  A later
    storage layer must commit both state changes atomically or commit neither.
    """

    reviewer_value, reviewed, decision_now = _review_context(reviewer, reviewed_at, now)
    if candidate.kind == "WORKING" or prior.kind == "WORKING":
        raise _decision_error("INVALID_DECISION", "working memory cannot be corrected durably")
    if candidate.memory_id is None or prior.memory_id is None:
        raise _decision_error("INVALID_DECISION", "correction and prior memory IDs are required")
    if candidate.memory_id == prior.memory_id:
        raise _decision_error("INVALID_DECISION", "correction must use a new memory ID")
    if candidate.supersedes_memory_id != prior.memory_id:
        raise _decision_error("INVALID_DECISION", "correction predecessor does not match")
    if candidate.scope != prior.scope:
        raise _decision_error("INVALID_DECISION", "correction scope must match its predecessor")
    if candidate.kind != prior.kind:
        raise _decision_error("INVALID_DECISION", "correction kind must match its predecessor")
    if candidate.version != prior.version + 1:
        raise _decision_error("INVALID_DECISION", "correction version must be consecutive")
    if prior.reviewer is None or prior.reviewed_at is None:
        raise _decision_error("INVALID_REVIEW", "approved predecessor lacks review evidence")
    if prior.reviewed_at > decision_now:
        raise _decision_error("INVALID_REVIEW", "approved predecessor review is in the future")
    _ensure_not_expired(candidate, decision_now, "correction")
    _ensure_not_expired(prior, decision_now, "prior")
    _ensure_review_chronology(candidate, reviewed)

    new_candidate_state, new_candidate_version = transition_state(
        candidate.state,
        "APPROVED",
        state_version=candidate.state_version,
        expected_version=expected_candidate_version,
    )
    new_prior_state, new_prior_version = transition_state(
        prior.state,
        "SUPERSEDED",
        state_version=prior.state_version,
        expected_version=expected_prior_version,
    )

    approved_correction = _rebuild(
        candidate,
        {
            "state": new_candidate_state,
            "state_version": new_candidate_version,
            "reviewer": reviewer_value,
            "reviewed_at": reviewed,
        },
    )
    superseded_prior = _rebuild(
        prior,
        {"state": new_prior_state, "state_version": new_prior_version},
    )
    return approved_correction, superseded_prior


__all__ = ["approve_candidate", "approve_correction", "reject_candidate"]
