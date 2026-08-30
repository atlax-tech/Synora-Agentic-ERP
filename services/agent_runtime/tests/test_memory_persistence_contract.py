"""Storage-neutral Memory persistence contract tests for Phase 8."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from agent_runtime.memory import (
    AtomicCorrectionCommand,
    CandidateInsertCommand,
    MemoryPersistenceError,
    MemoryPersistencePort,
    SingleRecordCasCommand,
    approve_correction,
)
from agent_runtime.memory.contracts import MemoryRecord, MemoryScope

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


def _construct(record: MemoryRecord, **overrides: object) -> MemoryRecord:
    values = dict(record.model_dump())
    values["scope"] = record.scope
    values.update(overrides)
    return MemoryRecord.model_construct(**values)


@pytest.mark.parametrize("kind", ["SEMANTIC", "EPISODIC", "PROCEDURAL"])
def test_candidate_insert_accepts_only_unreviewed_durable_candidates(kind: str) -> None:
    command = CandidateInsertCommand(_candidate(kind=kind))
    assert command.record.state == "CANDIDATE"
    assert command.record.state_version == 1
    assert command.record.content_classification == "UNTRUSTED"


@pytest.mark.parametrize(
    "record",
    [
        _candidate(kind="WORKING"),
        _approved(),
        _approved(state="REJECTED"),
        _approved(state="SUPERSEDED"),
        _approved(state="EXPIRED"),
        _approved(state="DELETED"),
    ],
)
def test_candidate_insert_rejects_working_and_non_candidate_states(record: MemoryRecord) -> None:
    with pytest.raises(MemoryPersistenceError):
        CandidateInsertCommand(record)


def test_candidate_insert_rejects_review_metadata_and_invalid_initial_version() -> None:
    with pytest.raises(MemoryPersistenceError):
        CandidateInsertCommand(
            _candidate(
                reviewer="system.manager@example.com",
                reviewed_at=NOW,
            )
        )
    with pytest.raises(MemoryPersistenceError):
        CandidateInsertCommand(_construct(_candidate(), state_version=0))


def test_candidate_insert_preserves_ids_and_does_not_invent_expiry() -> None:
    existing = CandidateInsertCommand(_candidate(memory_id="memory-existing"))
    unassigned = CandidateInsertCommand(_candidate(memory_id=None))
    expiring = CandidateInsertCommand(_candidate(expires_at=NOW + timedelta(days=1)))
    assert existing.record.memory_id == "memory-existing"
    assert unassigned.record.memory_id is None
    assert expiring.record.expires_at == NOW + timedelta(days=1)


def test_persistence_commands_are_frozen_and_port_has_only_narrow_methods() -> None:
    command = CandidateInsertCommand(_candidate())
    with pytest.raises(FrozenInstanceError):
        command.record = _candidate()  # type: ignore[misc]
    assert {
        "create_candidate",
        "get_exact",
        "commit_cas",
        "commit_correction_atomic",
    } <= set(MemoryPersistencePort.__dict__)
    assert not hasattr(MemoryPersistencePort, "search")
    assert not hasattr(MemoryPersistencePort, "execute_erp_write")


def test_single_record_cas_accepts_exact_one_step_version_update() -> None:
    updated = _candidate(
        state="APPROVED", state_version=2, reviewer="manager@example.com", reviewed_at=NOW
    )
    command = SingleRecordCasCommand(
        target_memory_id="memory-2",
        expected_state_version=1,
        updated=updated,
    )
    assert command.updated.state_version == command.expected_state_version + 1


@pytest.mark.parametrize("expected_version", [0, -1, True, False, 1.0, "1"])
def test_single_record_cas_rejects_invalid_expected_versions(expected_version: object) -> None:
    with pytest.raises(MemoryPersistenceError):
        SingleRecordCasCommand(
            target_memory_id="memory-2",
            expected_state_version=expected_version,  # type: ignore[arg-type]
            updated=_candidate(state_version=2),
        )


@pytest.mark.parametrize(
    "updated_version",
    [
        1,
        3,
    ],
)
def test_single_record_cas_rejects_same_version_and_version_jumps(updated_version: int) -> None:
    with pytest.raises(MemoryPersistenceError):
        SingleRecordCasCommand(
            target_memory_id="memory-2",
            expected_state_version=1,
            updated=_construct(_candidate(), state_version=updated_version),
        )


def test_single_record_cas_rejects_id_substitution_and_missing_ids() -> None:
    with pytest.raises(MemoryPersistenceError):
        SingleRecordCasCommand(
            target_memory_id="memory-other",
            expected_state_version=1,
            updated=_candidate(state_version=2),
        )
    with pytest.raises(MemoryPersistenceError):
        SingleRecordCasCommand(
            target_memory_id="memory-2",
            expected_state_version=1,
            updated=_construct(_candidate(), memory_id=None, state_version=2),
        )
    with pytest.raises(MemoryPersistenceError):
        SingleRecordCasCommand(
            target_memory_id="",
            expected_state_version=1,
            updated=_candidate(state_version=2),
        )


def _valid_correction_pair() -> tuple[MemoryRecord, MemoryRecord]:
    prior = _approved()
    candidate = _candidate(
        version=2,
        supersedes_memory_id=prior.memory_id,
        scope=prior.scope,
        kind=prior.kind,
    )
    return approve_correction(
        candidate,
        prior,
        reviewer="system.manager@example.com",
        reviewed_at=NOW,
        now=NOW,
        expected_candidate_version=1,
        expected_prior_version=1,
    )


def test_atomic_correction_accepts_a_valid_p8_m3_pair() -> None:
    corrected, superseded = _valid_correction_pair()
    command = AtomicCorrectionCommand(
        approved_correction=corrected,
        superseded_prior=superseded,
        expected_candidate_version=1,
        expected_prior_version=1,
    )
    assert command.approved_correction.state == "APPROVED"
    assert command.superseded_prior.state == "SUPERSEDED"


@pytest.mark.parametrize(
    ("candidate_overrides", "prior_overrides"),
    [
        ({"state_version": 3}, {}),
        ({}, {"state_version": 3}),
        ({"supersedes_memory_id": "other-memory"}, {}),
        ({"memory_id": "memory-1"}, {}),
        ({"scope": _scope(initiator="other@example.com")}, {}),
        ({"scope": _scope(company="Other Co")}, {}),
        ({"scope": _scope(warehouse="Stores - B")}, {}),
        ({"scope": _scope(run_id=None)}, {}),
        ({"kind": "PROCEDURAL"}, {}),
        ({"version": 3}, {}),
    ],
)
def test_atomic_correction_rejects_relationship_or_version_bypasses(
    candidate_overrides: dict[str, object], prior_overrides: dict[str, object]
) -> None:
    corrected, superseded = _valid_correction_pair()
    corrected = _construct(corrected, **candidate_overrides)
    superseded = _construct(superseded, **prior_overrides)
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            approved_correction=corrected,
            superseded_prior=superseded,
            expected_candidate_version=1,
            expected_prior_version=1,
        )


@pytest.mark.parametrize("wrong_state", [("CANDIDATE", "SUPERSEDED"), ("APPROVED", "APPROVED")])
def test_atomic_correction_rejects_wrong_result_states(wrong_state: tuple[str, str]) -> None:
    corrected, superseded = _valid_correction_pair()
    corrected = _construct(corrected, state=wrong_state[0])
    superseded = _construct(superseded, state=wrong_state[1])
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            approved_correction=corrected,
            superseded_prior=superseded,
            expected_candidate_version=1,
            expected_prior_version=1,
        )


def test_atomic_correction_rejects_working_records_and_invalid_ids() -> None:
    corrected, superseded = _valid_correction_pair()
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            approved_correction=_construct(corrected, kind="WORKING"),
            superseded_prior=superseded,
            expected_candidate_version=1,
            expected_prior_version=1,
        )
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            approved_correction=_construct(corrected, memory_id=None),
            superseded_prior=superseded,
            expected_candidate_version=1,
            expected_prior_version=1,
        )
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            approved_correction=corrected,
            superseded_prior=_construct(superseded, memory_id=None),
            expected_candidate_version=1,
            expected_prior_version=1,
        )


def test_atomic_correction_rejects_invalid_expected_versions_and_untrusted_bypass() -> None:
    corrected, superseded = _valid_correction_pair()
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            approved_correction=corrected,
            superseded_prior=superseded,
            expected_candidate_version=2,
            expected_prior_version=1,
        )
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            approved_correction=_construct(corrected, content_classification="AUTHORITY"),
            superseded_prior=superseded,
            expected_candidate_version=1,
            expected_prior_version=1,
        )


def test_invalid_model_constructed_record_is_revalidated_at_persistence_boundary() -> None:
    invalid = _construct(_candidate(), digest="0" * 64)
    with pytest.raises(MemoryPersistenceError):
        CandidateInsertCommand(invalid)
