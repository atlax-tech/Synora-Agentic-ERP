"""Storage-neutral Memory persistence contract tests for Phase 8."""

import hashlib
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
    reject_candidate,
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


def _rebuild(record: MemoryRecord, **overrides: object) -> MemoryRecord:
    values = dict(record.model_dump())
    values["scope"] = record.scope
    values.update(overrides)
    if "content" in overrides and "digest" not in overrides:
        values["digest"] = hashlib.sha256(str(overrides["content"]).encode()).hexdigest()
    return MemoryRecord(**values)


def _rejected_candidate() -> tuple[MemoryRecord, MemoryRecord]:
    candidate = _candidate()
    rejected = reject_candidate(
        candidate,
        reviewer="buyer@example.com",
        reviewed_at=NOW,
        now=NOW,
        expected_version=1,
        review_reason="Not reusable",
    )
    return candidate, rejected


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
    current, updated = _rejected_candidate()
    command = SingleRecordCasCommand(
        current=current,
        updated=updated,
        expected_state_version=1,
        target_memory_id="memory-2",
    )
    assert command.updated.state_version == command.expected_state_version + 1
    assert command.current == current


@pytest.mark.parametrize("expected_version", [0, -1, True, False, 1.0, "1"])
def test_single_record_cas_rejects_invalid_expected_versions(expected_version: object) -> None:
    current, updated = _rejected_candidate()
    with pytest.raises(MemoryPersistenceError):
        SingleRecordCasCommand(
            current=current,
            updated=updated,
            expected_state_version=expected_version,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "updated_version",
    [
        1,
        3,
    ],
)
def test_single_record_cas_rejects_same_version_and_version_jumps(updated_version: int) -> None:
    current, rejected = _rejected_candidate()
    with pytest.raises(MemoryPersistenceError):
        SingleRecordCasCommand(
            current=current,
            updated=_construct(
                rejected,
                state_version=updated_version,
            ),
            expected_state_version=1,
        )


def test_single_record_cas_rejects_id_substitution_and_missing_ids() -> None:
    current, updated = _rejected_candidate()
    with pytest.raises(MemoryPersistenceError):
        SingleRecordCasCommand(
            current=current,
            updated=updated,
            expected_state_version=1,
            target_memory_id="memory-other",
        )
    with pytest.raises(MemoryPersistenceError):
        SingleRecordCasCommand(
            current=_construct(current, memory_id="memory-other"),
            updated=updated,
            expected_state_version=1,
        )
    with pytest.raises(MemoryPersistenceError):
        SingleRecordCasCommand(
            current=current,
            updated=_construct(updated, memory_id=None),
            expected_state_version=1,
        )
    with pytest.raises(MemoryPersistenceError):
        SingleRecordCasCommand(
            current=_construct(current, memory_id=None),
            updated=updated,
            expected_state_version=1,
        )


@pytest.mark.parametrize(
    "field",
    [
        "scope",
        "kind",
        "content",
        "source_run_id",
        "source_claim_id",
        "source_revision",
        "version",
        "supersedes_memory_id",
        "created_at",
        "expires_at",
        "content_classification",
    ],
)
def test_single_record_cas_rejects_immutable_field_substitution(field: str) -> None:
    current, updated = _rejected_candidate()
    replacements: dict[str, object] = {
        "scope": _scope(company="Other Co"),
        "kind": "PROCEDURAL",
        "content": "A different but valid memory",
        "source_run_id": UUID("0c9f1a58-61d4-4b5d-a40f-f4a41ec7bc3d"),
        "source_claim_id": "claim-other",
        "source_revision": "run-rev-other",
        "version": 2,
        "supersedes_memory_id": "prior-memory",
        "created_at": NOW - timedelta(days=2),
        "expires_at": NOW + timedelta(days=1),
        "content_classification": "AUTHORITY",
    }
    if field == "content_classification":
        changed = _construct(updated, **{field: replacements[field]})
    else:
        overrides = {field: replacements[field]}
        if field == "supersedes_memory_id":
            overrides["version"] = 2
        changed = _rebuild(updated, **overrides)
    with pytest.raises(MemoryPersistenceError):
        SingleRecordCasCommand(current=current, updated=changed, expected_state_version=1)


@pytest.mark.parametrize(
    "changed_scope",
    [
        _scope(initiator="other@example.com"),
        _scope(company="Other Co"),
        _scope(warehouse="Stores - B"),
        _scope(run_id=None),
    ],
)
def test_single_record_cas_rejects_each_scope_dimension_substitution(
    changed_scope: MemoryScope,
) -> None:
    current, updated = _rejected_candidate()
    with pytest.raises(MemoryPersistenceError):
        SingleRecordCasCommand(
            current=current,
            updated=_rebuild(updated, scope=changed_scope),
            expected_state_version=1,
        )


@pytest.mark.parametrize(
    ("current", "updated"),
    [
        (
            _candidate(),
            _rebuild(
                _candidate(),
                state="REJECTED",
                state_version=2,
                reviewer="buyer@example.com",
                reviewed_at=NOW,
            ),
        ),
        (
            _approved(),
            _rebuild(_approved(), state="EXPIRED", state_version=2),
        ),
        (
            _rebuild(_approved(), state="EXPIRED", state_version=2),
            _rebuild(_approved(), state="DELETED", state_version=3),
        ),
    ],
)
def test_single_record_cas_accepts_legal_lifecycle_updates(
    current: MemoryRecord, updated: MemoryRecord
) -> None:
    command = SingleRecordCasCommand(
        current=current,
        updated=updated,
        expected_state_version=current.state_version,
    )
    assert command.updated.state_version == command.current.state_version + 1


def test_single_record_cas_rejects_stale_current_and_illegal_transition() -> None:
    current, updated = _rejected_candidate()
    with pytest.raises(MemoryPersistenceError):
        SingleRecordCasCommand(current=current, updated=updated, expected_state_version=2)
    with pytest.raises(MemoryPersistenceError):
        SingleRecordCasCommand(
            current=current,
            updated=_rebuild(current, state="SUPERSEDED", state_version=2),
            expected_state_version=1,
        )


def _valid_correction_pair() -> tuple[MemoryRecord, MemoryRecord, MemoryRecord, MemoryRecord]:
    prior_before = _approved()
    candidate_before = _candidate(
        version=2,
        supersedes_memory_id=prior_before.memory_id,
        scope=prior_before.scope,
        kind=prior_before.kind,
    )
    corrected, superseded = approve_correction(
        candidate_before,
        prior_before,
        reviewer="system.manager@example.com",
        reviewed_at=NOW,
        now=NOW,
        expected_candidate_version=1,
        expected_prior_version=1,
    )
    return candidate_before, prior_before, corrected, superseded


def test_atomic_correction_accepts_a_valid_p8_m3_pair() -> None:
    candidate_before, prior_before, corrected, superseded = _valid_correction_pair()
    command = AtomicCorrectionCommand(
        candidate_before=candidate_before,
        prior_before=prior_before,
        approved_correction=corrected,
        superseded_prior=superseded,
        expected_candidate_version=1,
        expected_prior_version=1,
    )
    assert command.approved_correction.state == "APPROVED"
    assert command.superseded_prior.state == "SUPERSEDED"
    assert command.candidate_before.state == "CANDIDATE"
    assert command.prior_before.state == "APPROVED"


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
    candidate_before, prior_before, corrected, superseded = _valid_correction_pair()
    corrected = _construct(corrected, **candidate_overrides)
    superseded = _construct(superseded, **prior_overrides)
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            candidate_before=candidate_before,
            prior_before=prior_before,
            approved_correction=corrected,
            superseded_prior=superseded,
            expected_candidate_version=1,
            expected_prior_version=1,
        )


@pytest.mark.parametrize("wrong_state", [("CANDIDATE", "SUPERSEDED"), ("APPROVED", "APPROVED")])
def test_atomic_correction_rejects_wrong_result_states(wrong_state: tuple[str, str]) -> None:
    candidate_before, prior_before, corrected, superseded = _valid_correction_pair()
    corrected = _construct(corrected, state=wrong_state[0])
    superseded = _construct(superseded, state=wrong_state[1])
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            candidate_before=candidate_before,
            prior_before=prior_before,
            approved_correction=corrected,
            superseded_prior=superseded,
            expected_candidate_version=1,
            expected_prior_version=1,
        )


def test_atomic_correction_rejects_working_records_and_invalid_ids() -> None:
    candidate_before, prior_before, corrected, superseded = _valid_correction_pair()
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            candidate_before=candidate_before,
            prior_before=prior_before,
            approved_correction=_construct(corrected, kind="WORKING"),
            superseded_prior=superseded,
            expected_candidate_version=1,
            expected_prior_version=1,
        )
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            candidate_before=candidate_before,
            prior_before=prior_before,
            approved_correction=_construct(corrected, memory_id=None),
            superseded_prior=superseded,
            expected_candidate_version=1,
            expected_prior_version=1,
        )
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            candidate_before=candidate_before,
            prior_before=prior_before,
            approved_correction=corrected,
            superseded_prior=_construct(superseded, memory_id=None),
            expected_candidate_version=1,
            expected_prior_version=1,
        )


def test_atomic_correction_rejects_invalid_expected_versions_and_untrusted_bypass() -> None:
    candidate_before, prior_before, corrected, superseded = _valid_correction_pair()
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            candidate_before=candidate_before,
            prior_before=prior_before,
            approved_correction=corrected,
            superseded_prior=superseded,
            expected_candidate_version=2,
            expected_prior_version=1,
        )
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            candidate_before=candidate_before,
            prior_before=prior_before,
            approved_correction=_construct(corrected, content_classification="AUTHORITY"),
            superseded_prior=superseded,
            expected_candidate_version=1,
            expected_prior_version=1,
        )


@pytest.mark.parametrize(
    (
        "candidate_before_overrides",
        "prior_before_overrides",
        "corrected_overrides",
        "superseded_overrides",
    ),
    [
        ({}, {}, {"content": "Different approved text"}, {}),
        ({}, {}, {}, {"content": "Different superseded text"}),
        ({}, {}, {"scope": _scope(company="Other Co")}, {}),
        ({}, {}, {}, {"scope": _scope(warehouse="Stores - B")}),
        ({}, {}, {"version": 3}, {}),
        ({"state_version": 2}, {}, {}, {}),
        (
            {
                "state": "REJECTED",
                "state_version": 2,
                "reviewer": "buyer@example.com",
                "reviewed_at": NOW,
            },
            {},
            {},
            {},
        ),
        (
            {},
            {},
            {
                "state": "REJECTED",
                "state_version": 2,
                "reviewer": "buyer@example.com",
                "reviewed_at": NOW,
            },
            {},
        ),
        ({}, {}, {"supersedes_memory_id": "other-memory"}, {}),
    ],
)
def test_atomic_correction_rejects_snapshot_or_result_substitution(
    candidate_before_overrides: dict[str, object],
    prior_before_overrides: dict[str, object],
    corrected_overrides: dict[str, object],
    superseded_overrides: dict[str, object],
) -> None:
    candidate_before, prior_before, corrected, superseded = _valid_correction_pair()
    candidate_before = _construct(candidate_before, **candidate_before_overrides)
    prior_before = _construct(prior_before, **prior_before_overrides)
    corrected = _rebuild(corrected, **corrected_overrides)
    superseded = _rebuild(superseded, **superseded_overrides)
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            candidate_before=candidate_before,
            prior_before=prior_before,
            approved_correction=corrected,
            superseded_prior=superseded,
            expected_candidate_version=1,
            expected_prior_version=1,
        )


def test_atomic_correction_rejects_each_scope_dimension_substitution() -> None:
    candidate_before, prior_before, corrected, superseded = _valid_correction_pair()
    for changed_scope in (
        _scope(initiator="other@example.com"),
        _scope(company="Other Co"),
        _scope(warehouse="Stores - B"),
        _scope(run_id=None),
    ):
        with pytest.raises(MemoryPersistenceError):
            AtomicCorrectionCommand(
                candidate_before=candidate_before,
                prior_before=prior_before,
                approved_correction=_rebuild(corrected, scope=changed_scope),
                superseded_prior=superseded,
                expected_candidate_version=1,
                expected_prior_version=1,
            )


def test_atomic_correction_rejects_stale_prior_and_inconsistent_before_relation() -> None:
    candidate_before, prior_before, corrected, superseded = _valid_correction_pair()
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            candidate_before=candidate_before,
            prior_before=_construct(prior_before, state_version=2),
            approved_correction=corrected,
            superseded_prior=superseded,
            expected_candidate_version=1,
            expected_prior_version=1,
        )
    with pytest.raises(MemoryPersistenceError):
        AtomicCorrectionCommand(
            candidate_before=_rebuild(candidate_before, supersedes_memory_id=None),
            prior_before=prior_before,
            approved_correction=corrected,
            superseded_prior=superseded,
            expected_candidate_version=1,
            expected_prior_version=1,
        )


def test_invalid_model_constructed_record_is_revalidated_at_persistence_boundary() -> None:
    invalid = _construct(_candidate(), digest="0" * 64)
    with pytest.raises(MemoryPersistenceError):
        CandidateInsertCommand(invalid)
