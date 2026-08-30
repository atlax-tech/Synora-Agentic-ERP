"""Strict, side-effect-free memory trust-boundary contracts.

This module intentionally contains no persistence, Frappe calls, retrieval
indexes, provider clients, or ERP tools.  It defines the data shape that those
later layers must enforce.  Memory content is untrusted and never an
authorization source.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

MemoryKind = Literal["WORKING", "EPISODIC", "SEMANTIC", "PROCEDURAL"]
MemoryState = Literal[
    "PENDING",
    "CANDIDATE",  # Compatibility spelling for the pre-review candidate state.
    "APPROVED",
    "REJECTED",
    "SUPERSEDED",
    "EXPIRED",
    "DELETED",
]
ContentClassification = Literal["UNTRUSTED"]

MEMORY_KINDS = frozenset({"WORKING", "EPISODIC", "SEMANTIC", "PROCEDURAL"})
MEMORY_STATES = frozenset(
    {
        "PENDING",
        "CANDIDATE",
        "APPROVED",
        "REJECTED",
        "SUPERSEDED",
        "EXPIRED",
        "DELETED",
    }
)
_ID = Annotated[str, Field(min_length=1, max_length=140)]
_Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)]


class StrictModel(BaseModel):
    """Shared strict, immutable base for public runtime contracts."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
        frozen=True,
    )


def _required_text(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


def _aware_timestamp(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class MemoryScope(StrictModel):
    """Exact identity/company/warehouse/run scope for a memory.

    ``owner`` is accepted only as a compatibility input alias for the existing
    Synora ``initiator`` authority.  Both names cannot disagree.
    """

    initiator: _ID = Field(validation_alias=AliasChoices("initiator", "owner"))
    company: _ID
    warehouse: _ID | None = None
    run_id: UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_conflicting_owner_aliases(cls, value: object) -> object:
        if isinstance(value, Mapping) and "initiator" in value and "owner" in value:
            if value["initiator"] != value["owner"]:
                raise ValueError("initiator and owner must identify the same user")
        return value

    @field_validator("initiator", "company", "warehouse")
    @classmethod
    def validate_scope_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return value
        field_name = getattr(info, "field_name", "scope field")
        return _required_text(value, str(field_name))

    @property
    def owner(self) -> str:
        """Return the existing initiator identity under the product synonym."""

        return self.initiator

    def matches(self, requested: MemoryScope) -> bool:
        """Match every scope dimension exactly; missing values never broaden."""

        return self == requested


class MemoryRecord(StrictModel):
    """Immutable candidate/version record for all non-authoritative memory."""

    memory_id: _ID | None = None
    kind: MemoryKind
    state: MemoryState = "PENDING"
    scope: MemoryScope
    source_run_id: UUID | None = None
    source_claim_id: _ID | None = None
    source_revision: _ID
    content: str = Field(min_length=1, max_length=32_000)
    content_classification: ContentClassification = "UNTRUSTED"
    digest: _Digest
    version: int = Field(default=1, ge=1, le=1_000_000)
    state_version: int = Field(default=1, ge=1, le=1_000_000)
    supersedes_memory_id: _ID | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    reviewed_at: datetime | None = None
    reviewer: _ID | None = None
    review_reason: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="before")
    @classmethod
    def fill_content_digest(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        if "digest" not in data and isinstance(data.get("content"), str):
            data["digest"] = hashlib.sha256(data["content"].encode("utf-8")).hexdigest()
        return data

    @field_validator("content", "source_revision", "review_reason")
    @classmethod
    def validate_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return value
        field_name = getattr(info, "field_name", "memory field")
        return _required_text(value, str(field_name))

    @field_validator("created_at", "expires_at", "reviewed_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None, info: object) -> datetime | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "timestamp")
        return _aware_timestamp(value, str(field_name))

    @model_validator(mode="after")
    def validate_invariants(self) -> MemoryRecord:
        expected_digest = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.digest != expected_digest:
            raise ValueError("memory digest does not match content")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        if self.reviewed_at is not None and self.reviewed_at < self.created_at:
            raise ValueError("reviewed_at must not precede created_at")
        if self.reviewer is not None and self.reviewed_at is None:
            raise ValueError("reviewer requires reviewed_at")
        if self.kind == "EPISODIC" and self.state == "APPROVED" and self.expires_at is None:
            raise ValueError("approved episodic memory requires an explicit expiry")
        if self.supersedes_memory_id is not None:
            if self.memory_id is None:
                raise ValueError("a correction must receive a new memory_id")
            if self.memory_id == self.supersedes_memory_id:
                raise ValueError("a correction cannot overwrite its superseded memory")
            if self.version < 2:
                raise ValueError("a correction must increment the memory version")
        if self.kind != "WORKING" and self.source_run_id is None and self.source_claim_id is None:
            raise ValueError("non-working memory requires a source run or claim")
        return self

    def is_recallable(self, requested_scope: MemoryScope, *, now: datetime | None = None) -> bool:
        """Return whether this record is safe to recall for ``requested_scope``."""

        return is_recallable(self, requested_scope, now=now)


def scope_matches(memory_scope: MemoryScope, requested_scope: MemoryScope) -> bool:
    """Compare every scope field exactly and fail closed on any difference."""

    return memory_scope.matches(requested_scope)


def is_recallable(
    memory: MemoryRecord,
    requested_scope: MemoryScope,
    *,
    now: datetime | None = None,
) -> bool:
    """Apply the pure recall gate used by later storage/retrieval layers.

    Only reviewed, approved, unexpired non-working memory with an exact scope
    is recallable.  The caller supplies a timestamp for deterministic tests;
    omitted timestamps use the current UTC clock and do not create a TTL.
    """

    if memory.kind == "WORKING" or memory.state != "APPROVED":
        return False
    current = now or datetime.now(UTC)
    try:
        current = _aware_timestamp(current, "now")
    except ValueError:
        return False
    if memory.expires_at is not None and current >= memory.expires_at:
        return False
    return scope_matches(memory.scope, requested_scope)
