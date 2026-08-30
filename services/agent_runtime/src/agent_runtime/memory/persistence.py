"""Storage-neutral commands for future durable Memory backends.

The commands validate the guarantees a backend must uphold without selecting
SQLite, Frappe, a database schema, or an authorization mechanism.  They do
not perform I/O; an adapter is responsible for implementing the protocol and
for making correction commits all-or-nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NoReturn, Protocol

from pydantic import ValidationError

from agent_runtime.memory.contracts import MemoryRecord

MemoryPersistenceErrorCode = Literal[
    "INVALID_COMMAND",
    "NOT_FOUND",
    "CONFLICT",
    "STALE_VERSION",
    "ATOMIC_COMMIT_FAILED",
]


class MemoryPersistenceError(ValueError):
    """Bounded storage-domain error with no transport or backend coupling."""

    def __init__(self, code: MemoryPersistenceErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


def _fail(code: MemoryPersistenceErrorCode, message: str) -> NoReturn:
    raise MemoryPersistenceError(code, message)


def _validate_id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 140:
        _fail("INVALID_COMMAND", f"{field_name} must be non-blank text")
    return value


def _validate_version(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail("INVALID_COMMAND", f"{field_name} must be an integer >= 1")
    return value


def _validate_record(value: object, field_name: str) -> MemoryRecord:
    if not isinstance(value, MemoryRecord):
        _fail("INVALID_COMMAND", f"{field_name} must be a MemoryRecord")
    try:
        MemoryRecord(**value.model_dump())
    except (ValidationError, TypeError, ValueError) as exc:
        raise MemoryPersistenceError("INVALID_COMMAND", f"{field_name} is invalid") from exc
    return value


def _ensure_durable(record: MemoryRecord, field_name: str) -> None:
    if record.kind == "WORKING":
        _fail("INVALID_COMMAND", f"{field_name} cannot persist Working memory")
    if record.content_classification != "UNTRUSTED":
        _fail("INVALID_COMMAND", f"{field_name} must remain UNTRUSTED")


def _validate_candidate(record: object) -> MemoryRecord:
    candidate = _validate_record(record, "candidate")
    _ensure_durable(candidate, "candidate")
    if candidate.state != "CANDIDATE":
        _fail("INVALID_COMMAND", "candidate must be in CANDIDATE state")
    if candidate.state_version != 1:
        _fail("INVALID_COMMAND", "candidate state_version must start at 1")
    if candidate.reviewer is not None or candidate.reviewed_at is not None:
        _fail("INVALID_COMMAND", "candidate cannot contain review metadata")
    return candidate


@dataclass(frozen=True, slots=True)
class CandidateInsertCommand:
    """Validated request to insert one unreviewed durable candidate."""

    record: MemoryRecord

    def __post_init__(self) -> None:
        _validate_candidate(self.record)


@dataclass(frozen=True, slots=True)
class SingleRecordCasCommand:
    """Validated one-record replacement guarded by an expected state version."""

    target_memory_id: str
    expected_state_version: int
    updated: MemoryRecord

    def __post_init__(self) -> None:
        target_id = _validate_id(self.target_memory_id, "target_memory_id")
        expected = _validate_version(self.expected_state_version, "expected_state_version")
        updated = _validate_record(self.updated, "updated")
        _ensure_durable(updated, "updated")
        if updated.memory_id is None:
            _fail("INVALID_COMMAND", "updated memory must have a durable ID")
        if updated.memory_id != target_id:
            _fail("CONFLICT", "updated memory ID does not match target ID")
        if updated.state_version != expected + 1:
            _fail("CONFLICT", "updated state_version must equal expected version plus one")


@dataclass(frozen=True, slots=True)
class AtomicCorrectionCommand:
    """Validated all-or-nothing pair for approving and superseding a correction."""

    approved_correction: MemoryRecord
    superseded_prior: MemoryRecord
    expected_candidate_version: int
    expected_prior_version: int

    def __post_init__(self) -> None:
        expected_candidate = _validate_version(
            self.expected_candidate_version, "expected_candidate_version"
        )
        expected_prior = _validate_version(self.expected_prior_version, "expected_prior_version")
        correction = _validate_record(self.approved_correction, "approved_correction")
        prior = _validate_record(self.superseded_prior, "superseded_prior")
        _ensure_durable(correction, "approved_correction")
        _ensure_durable(prior, "superseded_prior")
        if correction.state != "APPROVED" or prior.state != "SUPERSEDED":
            _fail("CONFLICT", "correction pair has invalid lifecycle states")
        if correction.memory_id is None or prior.memory_id is None:
            _fail("INVALID_COMMAND", "correction pair requires both durable IDs")
        if correction.memory_id == prior.memory_id:
            _fail("CONFLICT", "correction pair requires distinct memory IDs")
        if correction.supersedes_memory_id != prior.memory_id:
            _fail("CONFLICT", "correction predecessor ID does not match")
        if correction.scope != prior.scope:
            _fail("CONFLICT", "correction pair scopes must match exactly")
        if correction.kind != prior.kind:
            _fail("CONFLICT", "correction pair kinds must match")
        if correction.version != prior.version + 1:
            _fail("CONFLICT", "correction version must be consecutive")
        if correction.state_version != expected_candidate + 1:
            _fail("CONFLICT", "correction state_version must equal expected plus one")
        if prior.state_version != expected_prior + 1:
            _fail("CONFLICT", "prior state_version must equal expected plus one")


class MemoryPersistencePort(Protocol):
    """Narrow adapter boundary; implementations must provide atomic commits."""

    async def create_candidate(self, command: CandidateInsertCommand) -> MemoryRecord:
        """Insert a candidate, assigning an ID only when the record has none."""

    async def get_exact(self, memory_id: str) -> MemoryRecord:
        """Load one exact ID or raise MemoryPersistenceError(NOT_FOUND)."""

    async def commit_cas(self, command: SingleRecordCasCommand) -> MemoryRecord:
        """Commit one replacement or raise a stale/conflict persistence error."""

    async def commit_correction_atomic(
        self, command: AtomicCorrectionCommand
    ) -> tuple[MemoryRecord, MemoryRecord]:
        """Commit both pair members or neither; partial success is forbidden."""


__all__ = [
    "AtomicCorrectionCommand",
    "CandidateInsertCommand",
    "MemoryPersistenceError",
    "MemoryPersistenceErrorCode",
    "MemoryPersistencePort",
    "SingleRecordCasCommand",
]
