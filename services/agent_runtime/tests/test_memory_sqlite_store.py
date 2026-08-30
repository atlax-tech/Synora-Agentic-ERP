"""SQLite adapter tests for the single-instance Memory boundary."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from stat import S_IMODE
from uuid import UUID

import pytest
from agent_runtime.memory import (
    AtomicCorrectionCommand,
    CandidateInsertCommand,
    MemoryPersistenceError,
    MemoryPersistencePort,
    MemoryRecord,
    MemoryScope,
    SingleRecordCasCommand,
    SQLiteMemoryStore,
    approve_candidate,
    approve_correction,
    reject_candidate,
)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
RUN_ID = UUID("37e1d8a5-1730-4ad0-bffd-217774ed9fab")


def _run[T](awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)


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


def _rebuild(record: MemoryRecord, **overrides: object) -> MemoryRecord:
    values = dict(record.model_dump())
    values["scope"] = record.scope
    values.update(overrides)
    return MemoryRecord(**values)


def _valid_correction_pair() -> tuple[MemoryRecord, MemoryRecord, MemoryRecord, MemoryRecord]:
    prior_before = _approved(state_version=2)
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
        expected_prior_version=2,
    )
    return candidate_before, prior_before, corrected, superseded


def _create(store: SQLiteMemoryStore, record: MemoryRecord) -> MemoryRecord:
    return _run(store.create_candidate(CandidateInsertCommand(record)))


def _seed_approved(store: SQLiteMemoryStore, approved: MemoryRecord) -> MemoryRecord:
    candidate = _rebuild(
        approved,
        state="CANDIDATE",
        state_version=1,
        reviewer=None,
        reviewed_at=None,
    )
    created = _create(store, candidate)
    result = approve_candidate(
        created,
        reviewer=approved.reviewer or "system.manager@example.com",
        reviewed_at=approved.reviewed_at or NOW - timedelta(days=1),
        now=NOW,
        expected_version=1,
    )
    assert result == approved
    return _run(
        store.commit_cas(
            SingleRecordCasCommand(current=created, updated=result, expected_state_version=1)
        )
    )


def _connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def test_explicit_path_creates_private_usable_db(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "memory.db"
    SQLiteMemoryStore(path)
    assert path.exists()
    assert S_IMODE(path.stat().st_mode) == 0o600
    with _connection(path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'memory_records'"
        ).fetchone()
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_missing_or_directory_configuration_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SYNORA_MEMORY_DB_PATH", raising=False)
    with pytest.raises(MemoryPersistenceError) as missing:
        SQLiteMemoryStore()
    assert missing.value.code == "INVALID_COMMAND"

    directory = tmp_path / "memory-directory"
    directory.mkdir()
    with pytest.raises(MemoryPersistenceError) as directory_error:
        SQLiteMemoryStore(directory)
    assert directory_error.value.code == "INVALID_COMMAND"
    assert str(directory) not in str(directory_error.value)


def test_memory_path_does_not_implicitly_reuse_workflow_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_path = tmp_path / "workflow.db"
    monkeypatch.setenv("SYNORA_WORKFLOW_DB_PATH", str(workflow_path))
    monkeypatch.delenv("SYNORA_MEMORY_DB_PATH", raising=False)
    with pytest.raises(MemoryPersistenceError):
        SQLiteMemoryStore()
    assert not workflow_path.exists()


def test_two_store_instances_observe_committed_state(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    first = SQLiteMemoryStore(path)
    second = SQLiteMemoryStore(path)
    stored = _create(first, _candidate())
    assert _run(second.get_exact(stored.memory_id or "")).memory_id == stored.memory_id


def test_candidate_survives_reopen_and_supplied_id_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    stored = _create(SQLiteMemoryStore(path), _candidate(memory_id="supplied-id"))
    reopened = SQLiteMemoryStore(path)
    assert _run(reopened.get_exact("supplied-id")) == stored


def test_candidate_backend_assigns_id_when_absent(tmp_path: Path) -> None:
    stored = _create(SQLiteMemoryStore(tmp_path / "memory.db"), _candidate(memory_id=None))
    assert stored.memory_id
    assert stored.memory_id != "memory-2"


def test_duplicate_candidate_create_conflicts_without_upsert(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    original = _create(store, _candidate(memory_id="duplicate-id"))
    with pytest.raises(MemoryPersistenceError) as duplicate:
        _create(store, _candidate(memory_id="duplicate-id", content="another valid text"))
    assert duplicate.value.code == "CONFLICT"
    assert _run(store.get_exact("duplicate-id")) == original


def test_invalid_working_or_preapproved_command_never_reaches_storage(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    with pytest.raises(MemoryPersistenceError):
        CandidateInsertCommand(_candidate(kind="WORKING"))
    with pytest.raises(MemoryPersistenceError):
        CandidateInsertCommand(_approved())
    with _connection(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_records").fetchone()[0] == 0


def test_exact_load_round_trips_all_record_fields(tmp_path: Path) -> None:
    record = _candidate(
        memory_id="complete-record",
        scope=_scope(warehouse=None, run_id=None),
        source_claim_id="claim-complete",
        source_revision="revision-complete",
        content="A complete candidate payload.",
        version=2,
        supersedes_memory_id="prior-memory",
        expires_at=NOW + timedelta(days=3),
    )
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    assert _create(store, record) == record
    assert _run(store.get_exact("complete-record")) == record


def test_unknown_exact_id_returns_not_found(tmp_path: Path) -> None:
    with pytest.raises(MemoryPersistenceError) as missing:
        _run(SQLiteMemoryStore(tmp_path / "memory.db").get_exact("unknown"))
    assert missing.value.code == "NOT_FOUND"


def test_corrupted_payload_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(path)
    stored = _create(store, _candidate(memory_id="corrupt-me"))
    with _connection(path) as connection:
        connection.execute(
            "UPDATE memory_records SET payload = ? WHERE memory_id = ?",
            ("{not-json", stored.memory_id),
        )
    with pytest.raises(MemoryPersistenceError) as corrupted:
        _run(store.get_exact("corrupt-me"))
    assert corrupted.value.code == "ATOMIC_COMMIT_FAILED"


def test_incompatible_schema_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(path)
    _create(store, _candidate(memory_id="bad-schema"))
    with _connection(path) as connection:
        connection.execute(
            "UPDATE memory_records SET schema_version = ? WHERE memory_id = ?",
            ("999", "bad-schema"),
        )
    with pytest.raises(MemoryPersistenceError) as incompatible:
        _run(store.get_exact("bad-schema"))
    assert incompatible.value.code == "ATOMIC_COMMIT_FAILED"


def test_cas_persists_legal_candidate_rejection_and_reopen_sees_it(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(path)
    current = _create(store, _candidate())
    updated = reject_candidate(
        current,
        reviewer="buyer@example.com",
        reviewed_at=NOW,
        now=NOW,
        expected_version=1,
    )
    command = SingleRecordCasCommand(current=current, updated=updated, expected_state_version=1)
    assert _run(store.commit_cas(command)) == updated
    assert _run(SQLiteMemoryStore(path).get_exact("memory-2")).state == "REJECTED"


def test_cas_persists_approved_to_expired(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(path)
    current = _create(store, _candidate(expires_at=NOW + timedelta(days=1)))
    approved = approve_candidate(
        current,
        reviewer="system.manager@example.com",
        reviewed_at=NOW,
        now=NOW,
        expected_version=1,
    )
    _run(
        store.commit_cas(
            SingleRecordCasCommand(current=current, updated=approved, expected_state_version=1)
        )
    )
    expired = _rebuild(approved, state="EXPIRED", state_version=3)
    assert (
        _run(
            store.commit_cas(
                SingleRecordCasCommand(current=approved, updated=expired, expected_state_version=2)
            )
        )
        == expired
    )


def test_stale_expected_snapshot_rejects_and_leaves_row_unchanged(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    current = _create(store, _candidate())
    approved = approve_candidate(
        current,
        reviewer="system.manager@example.com",
        reviewed_at=NOW,
        now=NOW,
        expected_version=1,
    )
    rejected = reject_candidate(
        current,
        reviewer="buyer@example.com",
        reviewed_at=NOW,
        now=NOW,
        expected_version=1,
    )
    _run(
        store.commit_cas(
            SingleRecordCasCommand(current=current, updated=approved, expected_state_version=1)
        )
    )
    with pytest.raises(MemoryPersistenceError) as stale:
        _run(
            store.commit_cas(
                SingleRecordCasCommand(current=current, updated=rejected, expected_state_version=1)
            )
        )
    assert stale.value.code == "STALE_VERSION"
    assert _run(store.get_exact("memory-2")) == approved


def test_same_version_external_payload_substitution_rejects(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(path)
    current = _create(store, _candidate())
    substituted = _candidate(content="Externally substituted but valid content.")
    with _connection(path) as connection:
        connection.execute(
            "UPDATE memory_records SET payload = ?, state = ?, state_version = ? "
            "WHERE memory_id = ?",
            (
                store._serialize(substituted),
                substituted.state,
                substituted.state_version,
                "memory-2",
            ),
        )
    updated = reject_candidate(
        current,
        reviewer="buyer@example.com",
        reviewed_at=NOW,
        now=NOW,
        expected_version=1,
    )
    with pytest.raises(MemoryPersistenceError) as stale:
        _run(
            store.commit_cas(
                SingleRecordCasCommand(current=current, updated=updated, expected_state_version=1)
            )
        )
    assert stale.value.code == "STALE_VERSION"
    assert _run(store.get_exact("memory-2")) == substituted


def test_two_store_instances_racing_cas_have_one_winner(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    first = SQLiteMemoryStore(path)
    second = SQLiteMemoryStore(path)
    current = _create(first, _candidate())
    approved = approve_candidate(
        current,
        reviewer="system.manager@example.com",
        reviewed_at=NOW,
        now=NOW,
        expected_version=1,
    )
    rejected = reject_candidate(
        current,
        reviewer="buyer@example.com",
        reviewed_at=NOW,
        now=NOW,
        expected_version=1,
    )
    _run(
        first.commit_cas(
            SingleRecordCasCommand(current=current, updated=approved, expected_state_version=1)
        )
    )
    with pytest.raises(MemoryPersistenceError):
        _run(
            second.commit_cas(
                SingleRecordCasCommand(current=current, updated=rejected, expected_state_version=1)
            )
        )
    assert _run(first.get_exact("memory-2")) == approved


def test_atomic_correction_commits_both_rows_and_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(path)
    candidate_before, prior_before, corrected, superseded = _valid_correction_pair()
    _create(store, candidate_before)
    _seed_approved(store, prior_before)
    command = AtomicCorrectionCommand(
        candidate_before=candidate_before,
        prior_before=prior_before,
        approved_correction=corrected,
        superseded_prior=superseded,
        expected_candidate_version=1,
        expected_prior_version=2,
    )
    assert _run(store.commit_correction_atomic(command)) == (corrected, superseded)
    reopened = SQLiteMemoryStore(path)
    assert _run(reopened.get_exact("memory-2")).state == "APPROVED"
    assert _run(reopened.get_exact("memory-1")).state == "SUPERSEDED"


def test_atomic_correction_stale_candidate_leaves_both_rows_unchanged(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    candidate_before, prior_before, corrected, superseded = _valid_correction_pair()
    _create(store, candidate_before)
    _seed_approved(store, prior_before)
    rejected = reject_candidate(
        candidate_before,
        reviewer="buyer@example.com",
        reviewed_at=NOW,
        now=NOW,
        expected_version=1,
    )
    _run(
        store.commit_cas(
            SingleRecordCasCommand(
                current=candidate_before, updated=rejected, expected_state_version=1
            )
        )
    )
    command = AtomicCorrectionCommand(
        candidate_before=candidate_before,
        prior_before=prior_before,
        approved_correction=corrected,
        superseded_prior=superseded,
        expected_candidate_version=1,
        expected_prior_version=2,
    )
    with pytest.raises(MemoryPersistenceError) as stale:
        _run(store.commit_correction_atomic(command))
    assert stale.value.code == "STALE_VERSION"
    assert _run(store.get_exact("memory-2")) == rejected
    assert _run(store.get_exact("memory-1")) == prior_before


def test_atomic_correction_stale_prior_leaves_candidate_unchanged(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    candidate_before, prior_before, corrected, superseded = _valid_correction_pair()
    _create(store, candidate_before)
    _seed_approved(store, prior_before)
    expired = _rebuild(prior_before, state="EXPIRED", state_version=3)
    _run(
        store.commit_cas(
            SingleRecordCasCommand(current=prior_before, updated=expired, expected_state_version=2)
        )
    )
    command = AtomicCorrectionCommand(
        candidate_before=candidate_before,
        prior_before=prior_before,
        approved_correction=corrected,
        superseded_prior=superseded,
        expected_candidate_version=1,
        expected_prior_version=2,
    )
    with pytest.raises(MemoryPersistenceError) as stale:
        _run(store.commit_correction_atomic(command))
    assert stale.value.code == "STALE_VERSION"
    assert _run(store.get_exact("memory-2")) == candidate_before
    assert _run(store.get_exact("memory-1")) == expired


@pytest.mark.parametrize("missing_id", ["memory-2", "memory-1"])
def test_atomic_correction_missing_side_changes_neither_existing_row(
    tmp_path: Path, missing_id: str
) -> None:
    path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(path)
    candidate_before, prior_before, corrected, superseded = _valid_correction_pair()
    _create(store, candidate_before)
    _seed_approved(store, prior_before)
    with _connection(path) as connection:
        connection.execute("DELETE FROM memory_records WHERE memory_id = ?", (missing_id,))
    command = AtomicCorrectionCommand(
        candidate_before=candidate_before,
        prior_before=prior_before,
        approved_correction=corrected,
        superseded_prior=superseded,
        expected_candidate_version=1,
        expected_prior_version=2,
    )
    with pytest.raises(MemoryPersistenceError) as missing:
        _run(store.commit_correction_atomic(command))
    assert missing.value.code == "NOT_FOUND"
    remaining_id = "memory-1" if missing_id == "memory-2" else "memory-2"
    expected = prior_before if remaining_id == "memory-1" else candidate_before
    assert _run(store.get_exact(remaining_id)) == expected


def test_atomic_correction_rolls_back_when_second_update_fails(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(path)
    candidate_before, prior_before, corrected, superseded = _valid_correction_pair()
    _create(store, candidate_before)
    _seed_approved(store, prior_before)
    with _connection(path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_prior_update BEFORE UPDATE ON memory_records "
            "WHEN OLD.memory_id = 'memory-1' "
            "BEGIN SELECT RAISE(ABORT, 'test second update failure'); END;"
        )
    command = AtomicCorrectionCommand(
        candidate_before=candidate_before,
        prior_before=prior_before,
        approved_correction=corrected,
        superseded_prior=superseded,
        expected_candidate_version=1,
        expected_prior_version=2,
    )
    with pytest.raises(MemoryPersistenceError) as failed:
        _run(store.commit_correction_atomic(command))
    assert failed.value.code == "ATOMIC_COMMIT_FAILED"
    assert _run(store.get_exact("memory-2")) == candidate_before
    assert _run(store.get_exact("memory-1")) == prior_before


def _accepts_memory_port(port: MemoryPersistencePort) -> None:
    del port


def test_sqlite_store_matches_narrow_persistence_port(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    _accepts_memory_port(store)
    assert not hasattr(store, "search")
    assert not hasattr(store, "list_all")
    assert not hasattr(store, "delete")
    assert not hasattr(store, "execute")
    assert not hasattr(store, "inject_failure")


def test_public_errors_do_not_include_database_filesystem_details(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(path)
    with pytest.raises(MemoryPersistenceError) as missing:
        _run(store.get_exact("not-there"))
    assert str(path) not in str(missing.value)


def test_serialized_payload_has_no_duplicate_authoritative_columns(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(path)
    _create(store, _candidate(memory_id="schema-check"))
    with _connection(path) as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(memory_records)").fetchall()
        }
    assert columns == {
        "memory_id",
        "state",
        "state_version",
        "schema_version",
        "payload",
        "updated_at",
    }
    with _connection(path) as connection:
        payload = connection.execute(
            "SELECT payload FROM memory_records WHERE memory_id = ?", ("schema-check",)
        ).fetchone()[0]
    assert json.loads(payload)["memory_id"] == "schema-check"
