"""Pure Memory review and correction decision tests for Phase 8."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from agent_runtime.memory import (
    MemoryRecord,
    MemoryScope,
    MemoryStateError,
    approve_candidate,
    approve_correction,
    reject_candidate,
)
from pydantic import ValidationError

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
RUN_ID = UUID("37e1d8a5-1730-4ad0-bffd-217774ed9fab")


def _scope(
    *,
    initiator: str = "buyer@example.com",
    company: str = "Acme",
    warehouse: str | None = "Stores - A",
    run_id: UUID | None = RUN_ID,
) -> MemoryScope:
    return MemoryScope(
        initiator=initiator,
        company=company,
        warehouse=warehouse,
        run_id=run_id,
    )


def _candidate(**overrides: object) -> MemoryRecord:
    values: dict[str, object] = {
        "memory_id": "memory-2",
        "kind": "SEMANTIC",
        "scope": _scope(),
        "source_run_id": RUN_ID,
        "source_claim_id": "claim-2",
        "source_revision": "run-rev-2",
        "content": "Use the revised replenishment SOP.",
        "created_at": NOW - timedelta(days=1),
    }
    values.update(overrides)
    return MemoryRecord(**values)


def _approved(**overrides: object) -> MemoryRecord:
    values: dict[str, object] = {
        "memory_id": "memory-1",
        "state": "APPROVED",
        "kind": "SEMANTIC",
        "scope": _scope(),
        "source_run_id": RUN_ID,
        "source_claim_id": "claim-1",
        "source_revision": "run-rev-1",
        "content": "Use the original replenishment SOP.",
        "created_at": NOW - timedelta(days=2),
        "reviewer": "system.manager@example.com",
        "reviewed_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=1),
    }
    values.update(overrides)
    return MemoryRecord(**values)


def test_approve_candidate_rebuilds_a_valid_immutable_result() -> None:
    candidate = _candidate()
    approved = approve_candidate(
        candidate,
        reviewer="system.manager@example.com",
        reviewed_at=NOW,
        now=NOW,
        expected_version=1,
    )

    assert approved.state == "APPROVED"
    assert approved.state_version == 2
    assert approved.reviewer == "system.manager@example.com"
    assert approved.reviewed_at == NOW
    assert approved.scope == candidate.scope
    assert approved.content == candidate.content
    assert approved.digest == candidate.digest
    assert approved.content_classification == "UNTRUSTED"
    assert candidate.state == "CANDIDATE"
    assert candidate.state_version == 1
    assert candidate.reviewer is None
    assert candidate.reviewed_at is None


def test_reject_candidate_records_review_reason_without_mutating_input() -> None:
    candidate = _candidate()
    rejected = reject_candidate(
        candidate,
        reviewer="system.manager@example.com",
        reviewed_at=NOW,
        now=NOW,
        expected_version=1,
        review_reason="Insufficient source evidence.",
    )

    assert rejected.state == "REJECTED"
    assert rejected.state_version == 2
    assert rejected.review_reason == "Insufficient source evidence."
    assert rejected.content == candidate.content
    assert candidate.state == "CANDIDATE"
    assert candidate.state_version == 1


@pytest.mark.parametrize("operation", [approve_candidate, reject_candidate])
def test_review_decisions_require_a_fresh_candidate_version(operation: object) -> None:
    candidate = _candidate()
    kwargs: dict[str, object] = {
        "reviewer": "system.manager@example.com",
        "reviewed_at": NOW,
        "now": NOW,
        "expected_version": 2,
    }
    if operation is reject_candidate:
        kwargs["review_reason"] = "Rejected by policy."
    with pytest.raises(MemoryStateError):
        operation(candidate, **kwargs)  # type: ignore[operator]
    assert candidate.state == "CANDIDATE"
    assert candidate.state_version == 1


@pytest.mark.parametrize("state", ["APPROVED", "REJECTED"])
def test_non_candidate_review_decisions_are_rejected(state: str) -> None:
    values: dict[str, object] = {"state": state}
    if state == "APPROVED":
        values.update(
            reviewer="system.manager@example.com",
            reviewed_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(days=1),
        )
    else:
        values.update(reviewer="system.manager@example.com", reviewed_at=NOW - timedelta(minutes=1))
    record = _candidate(**values)
    with pytest.raises(MemoryStateError):
        approve_candidate(
            record,
            reviewer="system.manager@example.com",
            reviewed_at=NOW,
            now=NOW,
            expected_version=1,
        )
    with pytest.raises(MemoryStateError):
        reject_candidate(
            record,
            reviewer="system.manager@example.com",
            reviewed_at=NOW,
            now=NOW,
            expected_version=1,
        )


@pytest.mark.parametrize("reviewer", ["", "   ", None, 123])
def test_blank_or_non_text_reviewer_is_rejected(reviewer: object) -> None:
    with pytest.raises(MemoryStateError):
        approve_candidate(
            _candidate(),
            reviewer=reviewer,  # type: ignore[arg-type]
            reviewed_at=NOW,
            now=NOW,
            expected_version=1,
        )


def test_naive_or_future_review_time_is_rejected() -> None:
    with pytest.raises(MemoryStateError):
        approve_candidate(
            _candidate(),
            reviewer="system.manager@example.com",
            reviewed_at=datetime(2026, 8, 31, 12, 0),
            now=NOW,
            expected_version=1,
        )
    with pytest.raises(MemoryStateError):
        approve_candidate(
            _candidate(),
            reviewer="system.manager@example.com",
            reviewed_at=NOW + timedelta(seconds=1),
            now=NOW,
            expected_version=1,
        )


def test_review_must_be_before_expiry_and_no_ttl_is_invented() -> None:
    with pytest.raises(MemoryStateError):
        approve_candidate(
            _candidate(expires_at=NOW + timedelta(hours=1)),
            reviewer="system.manager@example.com",
            reviewed_at=NOW + timedelta(hours=1),
            now=NOW,
            expected_version=1,
        )
    semantic = approve_candidate(
        _candidate(),
        reviewer="system.manager@example.com",
        reviewed_at=NOW,
        now=NOW,
        expected_version=1,
    )
    assert semantic.expires_at is None
    with pytest.raises(MemoryStateError):
        approve_candidate(
            _candidate(kind="EPISODIC"),
            reviewer="system.manager@example.com",
            reviewed_at=NOW,
            now=NOW,
            expected_version=1,
        )


def test_correction_approval_returns_new_approved_and_old_superseded_records() -> None:
    prior = _approved()
    candidate = _candidate(
        memory_id="memory-2",
        version=2,
        supersedes_memory_id="memory-1",
        scope=prior.scope,
        kind=prior.kind,
    )
    corrected, superseded = approve_correction(
        candidate,
        prior,
        reviewer="system.manager@example.com",
        reviewed_at=NOW,
        now=NOW,
        expected_candidate_version=1,
        expected_prior_version=1,
    )

    assert corrected.state == "APPROVED"
    assert corrected.state_version == 2
    assert corrected.version == 2
    assert corrected.reviewer == "system.manager@example.com"
    assert corrected.reviewed_at == NOW
    assert corrected.content_classification == "UNTRUSTED"
    assert superseded.state == "SUPERSEDED"
    assert superseded.state_version == 2
    assert superseded.content == prior.content
    assert superseded.version == prior.version
    assert superseded.source_revision == prior.source_revision
    assert candidate.state == "CANDIDATE"
    assert candidate.state_version == 1
    assert prior.state == "APPROVED"
    assert prior.state_version == 1


@pytest.mark.parametrize(
    ("candidate_overrides", "prior_overrides"),
    [
        ({"supersedes_memory_id": "other-memory"}, {}),
        ({"memory_id": None}, {}),
        ({"supersedes_memory_id": "memory-1", "memory_id": None}, {}),
        ({"memory_id": "memory-1", "supersedes_memory_id": "other-memory"}, {}),
        ({"scope": _scope(initiator="other@example.com")}, {}),
        ({"scope": _scope(company="Other Co")}, {}),
        ({"scope": _scope(warehouse="Stores - B")}, {}),
        ({"scope": _scope(run_id=None)}, {}),
        ({"kind": "PROCEDURAL"}, {}),
        ({"version": 3}, {}),
        ({}, {"expires_at": NOW}),
        ({"expires_at": NOW}, {}),
    ],
)
def test_invalid_correction_relationships_are_rejected(
    candidate_overrides: dict[str, object], prior_overrides: dict[str, object]
) -> None:
    prior = _approved(**prior_overrides)
    candidate_values = {
        "memory_id": "memory-2",
        "version": 2,
        "supersedes_memory_id": "memory-1",
        "scope": prior.scope,
        "kind": prior.kind,
    }
    candidate_values.update(candidate_overrides)
    if candidate_values.get("memory_id") in {None, "memory-1"}:
        base = _candidate(
            memory_id="memory-2",
            version=2,
            supersedes_memory_id="memory-1",
            scope=prior.scope,
            kind=prior.kind,
        )
        raw_candidate = dict(base.model_dump())
        raw_candidate.update(candidate_values)
        candidate = MemoryRecord.model_construct(**raw_candidate)
    else:
        candidate = _candidate(**candidate_values)
    with pytest.raises(MemoryStateError):
        approve_correction(
            candidate,
            prior,
            reviewer="system.manager@example.com",
            reviewed_at=NOW,
            now=NOW,
            expected_candidate_version=1,
            expected_prior_version=1,
        )


@pytest.mark.parametrize(
    ("expected_candidate_version", "expected_prior_version"),
    [(2, 1), (1, 2)],
)
def test_correction_cas_rejects_stale_side_without_partial_transition(
    expected_candidate_version: int, expected_prior_version: int
) -> None:
    prior = _approved()
    candidate = _candidate(
        version=2,
        supersedes_memory_id=prior.memory_id,
        scope=prior.scope,
        kind=prior.kind,
    )
    with pytest.raises(MemoryStateError):
        approve_correction(
            candidate,
            prior,
            reviewer="system.manager@example.com",
            reviewed_at=NOW,
            now=NOW,
            expected_candidate_version=expected_candidate_version,
            expected_prior_version=expected_prior_version,
        )
    assert candidate.state == "CANDIDATE"
    assert candidate.state_version == 1
    assert prior.state == "APPROVED"
    assert prior.state_version == 1


def test_correction_does_not_accept_a_future_reviewed_predecessor() -> None:
    prior = _approved(reviewed_at=NOW + timedelta(minutes=1))
    candidate = _candidate(
        version=2,
        supersedes_memory_id=prior.memory_id,
        scope=prior.scope,
        kind=prior.kind,
    )
    with pytest.raises(MemoryStateError):
        approve_correction(
            candidate,
            prior,
            reviewer="system.manager@example.com",
            reviewed_at=NOW,
            now=NOW,
            expected_candidate_version=1,
            expected_prior_version=1,
        )


def test_working_memory_cannot_be_promoted_to_durable_review() -> None:
    with pytest.raises(MemoryStateError):
        approve_candidate(
            _candidate(kind="WORKING"),
            reviewer="system.manager@example.com",
            reviewed_at=NOW,
            now=NOW,
            expected_version=1,
        )
    with pytest.raises(MemoryStateError):
        reject_candidate(
            _candidate(kind="WORKING"),
            reviewer="system.manager@example.com",
            reviewed_at=NOW,
            now=NOW,
            expected_version=1,
        )


def test_contract_rejects_same_identity_correction_before_decision() -> None:
    with pytest.raises(ValidationError):
        _candidate(memory_id="memory-1", version=2, supersedes_memory_id="memory-1")
