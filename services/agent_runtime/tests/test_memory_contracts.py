"""Phase 8 memory trust-boundary contract tests.

These tests deliberately exercise the pure contract layer only.  Persistence,
Frappe permissions, retrieval indexes, and providers are deferred to later
Phase 8 increments.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from agent_runtime.memory.contracts import (
    MemoryRecord,
    MemoryScope,
    is_recallable,
)
from pydantic import ValidationError

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
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


def _record(**overrides: object) -> MemoryRecord:
    values: dict[str, object] = {
        "memory_id": "memory-1",
        "kind": "SEMANTIC",
        "state": "PENDING",
        "scope": _scope(),
        "source_run_id": RUN_ID,
        "source_claim_id": "claim-1",
        "source_revision": "run-rev-1",
        "content": "Use the approved replenishment SOP.",
        "created_at": NOW,
    }
    values.update(overrides)
    return MemoryRecord(**values)


def test_all_approved_memory_kinds_are_accepted() -> None:
    for kind in ("WORKING", "EPISODIC", "SEMANTIC", "PROCEDURAL"):
        record = _record(kind=kind)
        assert record.kind == kind


def test_unknown_kind_state_and_extra_capability_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _record(kind="NOT_A_MEMORY_KIND")
    with pytest.raises(ValidationError):
        _record(state="APPROVE_ANYWAY")
    with pytest.raises(ValidationError):
        _record(allowed_tools=("purchase_order.submit",))


def test_candidate_and_pending_memory_are_not_recallable() -> None:
    for state in ("PENDING", "CANDIDATE"):
        record = _record(state=state)
        assert is_recallable(record, _scope(), now=NOW) is False


def test_approved_unexpired_exact_scope_is_recallable() -> None:
    record = _record(
        state="APPROVED",
        expires_at=NOW + timedelta(days=1),
    )
    assert is_recallable(record, _scope(), now=NOW) is True


def test_episodic_approval_requires_expiry_but_no_numeric_ttl_is_invented() -> None:
    with pytest.raises(ValidationError):
        _record(kind="EPISODIC", state="APPROVED")

    record = _record(kind="EPISODIC", state="APPROVED", expires_at=NOW + timedelta(hours=1))
    assert record.expires_at == NOW + timedelta(hours=1)


@pytest.mark.parametrize(
    ("scope", "query_scope"),
    [
        (_scope(initiator="buyer-a@example.com"), _scope(initiator="buyer-b@example.com")),
        (_scope(company="Company A"), _scope(company="Company B")),
        (_scope(warehouse="Stores - A"), _scope(warehouse=None)),
        (_scope(warehouse=None), _scope(warehouse="Stores - A")),
        (_scope(run_id=RUN_ID), _scope(run_id=None)),
        (_scope(run_id=None), _scope(run_id=RUN_ID)),
    ],
)
def test_scope_mismatch_never_broadens(scope: MemoryScope, query_scope: MemoryScope) -> None:
    record = _record(state="APPROVED", expires_at=NOW + timedelta(days=1), scope=scope)
    assert is_recallable(record, query_scope, now=NOW) is False


@pytest.mark.parametrize("state", ["REJECTED", "SUPERSEDED", "EXPIRED", "DELETED"])
def test_non_recallable_lifecycle_states_are_excluded(state: str) -> None:
    record = _record(state=state, expires_at=NOW + timedelta(days=1))
    assert is_recallable(record, _scope(), now=NOW) is False


def test_expiry_is_checked_at_supplied_time() -> None:
    record = _record(
        state="APPROVED",
        created_at=NOW - timedelta(days=1),
        expires_at=NOW,
    )
    assert is_recallable(record, _scope(), now=NOW) is False
    assert is_recallable(record, _scope(), now=NOW - timedelta(seconds=1)) is True


def test_expiry_metadata_must_be_timezone_aware_and_chronological() -> None:
    with pytest.raises(ValidationError):
        _record(expires_at=datetime(2026, 8, 31, 12, 0))
    with pytest.raises(ValidationError):
        _record(expires_at=NOW)
    with pytest.raises(ValidationError):
        _record(expires_at=NOW - timedelta(seconds=1))


def test_correction_links_a_new_identity_without_overwriting_the_old_record() -> None:
    old = _record(state="APPROVED", expires_at=NOW + timedelta(days=1))
    corrected = _record(
        memory_id="memory-2",
        version=2,
        supersedes_memory_id=old.memory_id,
        content="Use the revised replenishment SOP.",
    )
    assert corrected.memory_id != old.memory_id
    assert corrected.supersedes_memory_id == old.memory_id
    assert corrected.version == 2
    with pytest.raises(ValidationError):
        _record(memory_id="memory-1", version=2, supersedes_memory_id="memory-1")


def test_digest_is_content_bound_and_content_remains_untrusted() -> None:
    record = _record()
    assert len(record.digest) == 64
    assert record.content_classification == "UNTRUSTED"
    with pytest.raises(ValidationError):
        _record(digest="0" * 64)
    with pytest.raises(ValidationError):
        _record(content_classification="AUTHORITY")


def test_working_memory_is_never_recallable_or_authorizing() -> None:
    record = _record(kind="WORKING", state="APPROVED")
    assert is_recallable(record, _scope(), now=NOW) is False
    assert record.content_classification == "UNTRUSTED"


def test_owner_alias_uses_the_existing_initiator_authority() -> None:
    scope = MemoryScope(owner="buyer@example.com", company="Acme")
    assert scope.initiator == "buyer@example.com"
    assert scope.owner == scope.initiator
    with pytest.raises(ValidationError):
        MemoryScope(initiator="buyer-a@example.com", owner="buyer-b@example.com", company="Acme")


def test_memory_id_defaults_for_unpersisted_candidates_and_source_is_versioned() -> None:
    record = MemoryRecord(
        kind="SEMANTIC",
        scope=_scope(run_id=None),
        source_claim_id="claim-1",
        source_revision="knowledge-rev-1",
        content="A candidate without a database id is still versioned.",
    )
    assert record.memory_id is None
    assert record.version == 1
    assert record.state_version == 1
