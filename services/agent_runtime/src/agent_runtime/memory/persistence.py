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
from agent_runtime.memory.state import MemoryStateError, transition_state

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
        validated = MemoryRecord(**value.model_dump())
    except (ValidationError, TypeError, ValueError) as exc:
        raise MemoryPersistenceError("INVALID_COMMAND", f"{field_name} is invalid") from exc
    return validated


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


_IMMUTABLE_FIELDS = (
    "memory_id",
    "kind",
    "scope",
    "source_run_id",
    "source_claim_id",
    "source_revision",
    "content",
    "digest",
    "version",
    "supersedes_memory_id",
    "created_at",
    "expires_at",
    "content_classification",
)


def _ensure_immutable_fields(current: MemoryRecord, updated: MemoryRecord, field_name: str) -> None:
    for name in _IMMUTABLE_FIELDS:
        if getattr(current, name) != getattr(updated, name):
            _fail("CONFLICT", f"{field_name} immutable field cannot change")


def _ensure_lifecycle_update(current: MemoryRecord, updated: MemoryRecord, field_name: str) -> None:
    try:
        _, expected_state_version = transition_state(
            current.state,
            updated.state,
            state_version=current.state_version,
            expected_version=current.state_version,
        )
    except MemoryStateError as exc:
        raise MemoryPersistenceError("CONFLICT", f"{field_name} transition is invalid") from exc
    if updated.state_version != expected_state_version:
        _fail("CONFLICT", f"{field_name} state_version must increment by one")


@dataclass(frozen=True, slots=True)
class CandidateInsertCommand:
    """Validated request to insert one unreviewed durable candidate."""

    record: MemoryRecord

    def __post_init__(self) -> None:
        object.__setattr__(self, "record", _validate_candidate(self.record))


@dataclass(frozen=True, slots=True)
class SingleRecordCasCommand:
    """Validated one-record lifecycle update bound to an exact pre-state."""

    current: MemoryRecord
    updated: MemoryRecord
    expected_state_version: int
    target_memory_id: str | None = None

    def __post_init__(self) -> None:
        expected = _validate_version(self.expected_state_version, "expected_state_version")
        current = _validate_record(self.current, "current")
        updated = _validate_record(self.updated, "updated")
        _ensure_durable(current, "current")
        _ensure_durable(updated, "updated")

        if current.memory_id is None or updated.memory_id is None:
            _fail("INVALID_COMMAND", "current and updated memory must have durable IDs")
        if current.memory_id != updated.memory_id:
            _fail("CONFLICT", "current and updated memory IDs must match")
        if self.target_memory_id is not None:
            target_id = _validate_id(self.target_memory_id, "target_memory_id")
            if target_id != current.memory_id:
                _fail("CONFLICT", "target ID does not match current memory ID")
        if current.state_version != expected:
            _fail("STALE_VERSION", "current state_version does not match expected version")
        _ensure_immutable_fields(current, updated, "memory")
        _ensure_lifecycle_update(current, updated, "memory")
        if updated.state_version != expected + 1:
            _fail("CONFLICT", "updated state_version must equal expected version plus one")
        object.__setattr__(self, "current", current)
        object.__setattr__(self, "updated", updated)


@dataclass(frozen=True, slots=True)
class AtomicCorrectionCommand:
    """Validated all-or-nothing correction bound to both exact pre-state snapshots."""

    candidate_before: MemoryRecord
    prior_before: MemoryRecord
    approved_correction: MemoryRecord
    superseded_prior: MemoryRecord
    expected_candidate_version: int
    expected_prior_version: int

    def __post_init__(self) -> None:
        expected_candidate = _validate_version(
            self.expected_candidate_version, "expected_candidate_version"
        )
        expected_prior = _validate_version(self.expected_prior_version, "expected_prior_version")
        candidate_before = _validate_record(self.candidate_before, "candidate_before")
        prior_before = _validate_record(self.prior_before, "prior_before")
        correction = _validate_record(self.approved_correction, "approved_correction")
        prior = _validate_record(self.superseded_prior, "superseded_prior")
        _ensure_durable(candidate_before, "candidate_before")
        _ensure_durable(prior_before, "prior_before")
        _ensure_durable(correction, "approved_correction")
        _ensure_durable(prior, "superseded_prior")

        if (
            candidate_before.memory_id is None
            or prior_before.memory_id is None
            or correction.memory_id is None
            or prior.memory_id is None
        ):
            _fail("INVALID_COMMAND", "correction pair requires durable IDs")
        if candidate_before.memory_id != correction.memory_id:
            _fail("CONFLICT", "candidate snapshot and result IDs must match")
        if prior_before.memory_id != prior.memory_id:
            _fail("CONFLICT", "prior snapshot and result IDs must match")
        if candidate_before.memory_id == prior_before.memory_id:
            _fail("CONFLICT", "correction pair requires distinct memory IDs")
        if candidate_before.state != "CANDIDATE" or prior_before.state != "APPROVED":
            _fail("CONFLICT", "correction snapshots have invalid lifecycle states")
        if correction.state != "APPROVED" or prior.state != "SUPERSEDED":
            _fail("CONFLICT", "correction results have invalid lifecycle states")
        if candidate_before.state_version != expected_candidate:
            _fail("STALE_VERSION", "candidate snapshot does not match expected version")
        if prior_before.state_version != expected_prior:
            _fail("STALE_VERSION", "prior snapshot does not match expected version")
        _ensure_lifecycle_update(candidate_before, correction, "candidate correction")
        _ensure_lifecycle_update(prior_before, prior, "prior supersession")
        _ensure_immutable_fields(candidate_before, correction, "candidate correction")
        _ensure_immutable_fields(prior_before, prior, "prior supersession")
        if candidate_before.supersedes_memory_id != prior_before.memory_id:
            _fail("CONFLICT", "candidate snapshot predecessor ID does not match")
        if correction.supersedes_memory_id != prior.memory_id:
            _fail("CONFLICT", "correction predecessor ID does not match")
        if candidate_before.scope != prior_before.scope or correction.scope != prior.scope:
            _fail("CONFLICT", "correction pair scopes must match exactly")
        if candidate_before.kind != prior_before.kind or correction.kind != prior.kind:
            _fail("CONFLICT", "correction pair kinds must match")
        if candidate_before.version != prior_before.version + 1:
            _fail("CONFLICT", "candidate snapshot version must be consecutive")
        if correction.version != prior.version + 1:
            _fail("CONFLICT", "correction version must be consecutive")
        if correction.state_version != expected_candidate + 1:
            _fail("CONFLICT", "correction state_version must equal expected plus one")
        if prior.state_version != expected_prior + 1:
            _fail("CONFLICT", "prior state_version must equal expected plus one")
        object.__setattr__(self, "candidate_before", candidate_before)
        object.__setattr__(self, "prior_before", prior_before)
        object.__setattr__(self, "approved_correction", correction)
        object.__setattr__(self, "superseded_prior", prior)


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
