"""Frappe-authoritative Memory lifecycle and exact-scope recall.

This module is deliberately separate from the LAB_ONLY SQLite adapter.  It is
the only production-facing path that can create, review, correct, tombstone,
or recall durable Memory records.  The returned content remains UNTRUSTED and
is never treated as policy, authorization, or live ERP evidence.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

import frappe
from frappe.utils import get_datetime, now_datetime

from synora_agentic_erp.gateway.contract import GatewayFault, canonical_uuid
from synora_agentic_erp.synora_agentic_erp.doctype.synora_memory_record import (
    synora_memory_record as memory_record,
)

SERVICE_FLAG = memory_record.SERVICE_FLAG

MAX_PAGE_SIZE = 50
MAX_OFFSET = 10_000
MAX_REASON_LENGTH = 2_000
MAX_SOURCE_LENGTH = 140
MAX_CONTENT_LENGTH = memory_record.MAX_CONTENT_LENGTH
REVIEW_DECISIONS = frozenset({"APPROVE", "REJECT"})
MEMORY_KINDS = memory_record.DURABLE_KINDS

_MEMORY_FIELDS = [
    "name",
    "kind",
    "state",
    "initiator",
    "company_scope",
    "warehouse_scope",
    "scope_run",
    "source_run",
    "source_claim_id",
    "source_revision",
    "content",
    "content_classification",
    "digest",
    "dedupe_key",
    "memory_version",
    "state_version",
    "supersedes_memory",
    "expires_at",
    "reviewed_at",
    "reviewer",
    "review_reason",
    "deleted_at",
    "deleted_by",
    "deletion_reason",
    "creation",
    "modified",
    "modified_by",
    "owner",
    "docstatus",
    "idx",
]


def _actor() -> str:
    actor = str(getattr(frappe.session, "user", "Guest") or "Guest")
    if actor == "Guest":
        raise GatewayFault("AUTHENTICATION_REQUIRED", "authenticated user required", 401)
    if not frappe.db.get_value("User", actor, "enabled"):
        raise GatewayFault("AUTHENTICATION_REQUIRED", "authenticated user required", 401)
    return actor


def _not_available() -> GatewayFault:
    # The same response is used for unknown and out-of-scope records so callers
    # cannot probe Memory IDs.
    return GatewayFault("MEMORY_NOT_AVAILABLE", "memory is not available", 404)


def _required_text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or not value.strip():
        raise GatewayFault("INVALID_INPUT", f"{label} is invalid")
    return value


def _optional_text(value: object, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _required_text(value, label, maximum)


def _validated_source_claim(
    source_claim_id: str | None,
    *,
    source_run: str,
    source_revision: str,
    content: str,
    scope: dict[str, str | None],
) -> str | None:
    """Resolve claim provenance through Frappe; never trust an arbitrary ID."""
    if source_claim_id is None:
        return None
    from synora_agentic_erp.coach.service import resolve_coach_claim

    try:
        claim = resolve_coach_claim(
            source_claim_id,
            run_id=source_run,
            source_revision=source_revision,
        )
    except GatewayFault:
        raise _not_available() from None
    if (
        str(claim.get("run")) != str(scope["run_id"])
        or str(claim.get("initiator")) != str(scope["initiator"])
        or str(claim.get("company_scope")) != str(scope["company"])
        or (str(claim.get("warehouse_scope") or "") or None)
        != (str(scope["warehouse"] or "") or None)
        or str(claim.get("source_revision")) != source_revision
        or str(claim.get("claim_digest")) != hashlib.sha256(content.encode("utf-8")).hexdigest()
    ):
        raise _not_available()
    return str(claim["name"])


def _kind(value: object) -> str:
    kind = _required_text(value, "kind", 20)
    if kind not in MEMORY_KINDS:
        raise GatewayFault("INVALID_INPUT", "kind is invalid")
    return kind


def _memory_id(value: object) -> str:
    return canonical_uuid(value, "memory_id")


def _state_version(value: object, label: str = "expected_state_version") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GatewayFault("INVALID_INPUT", f"{label} is invalid")
    return value


def _reason(value: object) -> str | None:
    return _optional_text(value, "reason", MAX_REASON_LENGTH)


def _expiry(value: object, kind: str) -> str | None:
    if value is None:
        if kind == "EPISODIC":
            raise GatewayFault("INVALID_INPUT", "episodic memory requires an expiry")
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise GatewayFault("INVALID_INPUT", "expires_at is invalid")
    try:
        parsed = get_datetime(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise GatewayFault("INVALID_INPUT", "expires_at is invalid") from error
    if parsed <= now_datetime():
        raise GatewayFault("INVALID_INPUT", "expires_at must be in the future")
    return str(parsed)


def _is_system_manager(actor: str) -> bool:
    try:
        return "System Manager" in frappe.get_roles(actor)
    except Exception:
        return False


def _scope_readable(company: str, warehouse: str | None, actor: str) -> bool:
    if not company:
        return False
    try:
        if not frappe.has_permission("Company", "read", doc=company, user=actor):
            return False
        if not warehouse:
            return True
        row = frappe.db.get_value("Warehouse", warehouse, ["company", "disabled"], as_dict=True)
        return bool(
            row
            and str(row.company) == company
            and not row.disabled
            and frappe.has_permission("Warehouse", "read", doc=warehouse, user=actor)
        )
    except Exception:
        return False


def _run_scope(source_run: object, actor: str) -> dict[str, str | None]:
    safe_run = canonical_uuid(source_run, "source_run")
    row = frappe.db.get_value(
        "Synora Agent Run",
        safe_run,
        [
            "name",
            "initiator",
            "company_scope",
            "warehouse_scope",
            "correlation_id",
            "status",
            "revoked",
        ],
        as_dict=True,
    )
    if (
        not row
        or str(row.status) != "ACTIVE"
        or bool(row.revoked)
        or not row.initiator
        or (str(row.initiator) != actor and not _is_system_manager(actor))
        or not _scope_readable(str(row.company_scope or ""), row.warehouse_scope, actor)
    ):
        raise _not_available()
    return {
        "run_id": safe_run,
        "initiator": str(row.initiator),
        "company": str(row.company_scope),
        "warehouse": str(row.warehouse_scope or "") or None,
        "correlation_id": str(row.correlation_id or "") or None,
    }


def _can_review(memory: Any, actor: str) -> bool:
    return memory_record.can_review_memory(memory, actor)


def _can_manage(memory: Any, actor: str) -> bool:
    if not memory_record.can_read_memory(memory, actor):
        return False
    if memory.kind == "EPISODIC":
        return str(memory.initiator) == actor
    return memory.kind in {"SEMANTIC", "PROCEDURAL"} and _is_system_manager(actor)


def _is_expired(memory: Any) -> bool:
    return memory_record.is_expired(memory)


def _visible_doc(memory: Any, actor: str) -> bool:
    return (
        str(memory.state or "") == "PENDING"
        and not _is_expired(memory)
        and _can_review(memory, actor)
    )


def _has_active_pending_correction(memory_id: str) -> bool:
    """Keep an approved predecessor out of recall while its correction is pending."""
    try:
        rows = frappe.get_all(
            "Synora Memory Record",
            filters={"supersedes_memory": memory_id, "state": "PENDING"},
            fields=["kind", "expires_at"],
            limit_page_length=0,
            ignore_permissions=True,
        )
        return any(not _is_expired(row) for row in rows)
    except Exception:
        # A recall failure must not expose a predecessor that may have an
        # unobserved pending correction.
        return True


def _serialize(memory: Any) -> dict[str, Any]:
    deleted = str(memory.state) == "DELETED"
    return {
        "name": str(memory.name),
        "kind": str(memory.kind),
        "state": str(memory.state),
        "initiator": str(memory.initiator),
        "company_scope": str(memory.company_scope),
        "warehouse_scope": str(memory.warehouse_scope or "") or None,
        "scope_run": str(memory.scope_run or "") or None,
        "source_run": str(memory.source_run or "") or None,
        "source_claim_id": str(memory.source_claim_id or "") or None,
        "source_revision": str(memory.source_revision),
        # A tombstone never returns the deleted body.
        "content": None if deleted else str(memory.content),
        "content_classification": str(memory.content_classification),
        "digest": str(memory.digest),
        "dedupe_key": str(memory.dedupe_key or "") or None,
        "memory_version": int(memory.memory_version),
        "state_version": int(memory.state_version),
        "supersedes_memory": str(memory.supersedes_memory or "") or None,
        "expires_at": str(memory.expires_at or "") or None,
        "reviewed_at": str(memory.reviewed_at or "") or None,
        "reviewer": str(memory.reviewer or "") or None,
        "review_reason": str(memory.review_reason or "") or None,
        "deleted_at": str(memory.deleted_at or "") or None,
        "deleted_by": str(memory.deleted_by or "") or None,
        "deletion_reason": str(memory.deletion_reason or "") or None,
        "created_at": str(memory.creation or ""),
    }


def _serialize_review_candidate(memory: Any, actor: str) -> dict[str, Any]:
    """Add only the predecessor CAS value needed to review a correction."""
    serialized = _serialize(memory)
    predecessor_id = str(memory.supersedes_memory or "") or None
    if predecessor_id:
        predecessor = _load_memory(predecessor_id)
        if not memory_record.can_read_memory(predecessor, actor):
            raise _not_available()
        serialized["predecessor_state_version"] = int(predecessor.state_version)
    return serialized


def _load_memory(memory_id: str) -> Any:
    row = frappe.db.get_value(
        "Synora Memory Record",
        memory_id,
        _MEMORY_FIELDS,
        as_dict=True,
    )
    if not row:
        raise _not_available()
    row["doctype"] = "Synora Memory Record"
    return frappe.get_doc(row)


def _save_service(doc: Any) -> Any:
    """Save while allowing Frappe's timestamp reload through native read hooks."""
    previous = getattr(frappe.flags, "synora_memory_service_read", False)
    frappe.flags.synora_memory_service_read = True
    try:
        return doc.save(ignore_permissions=True)
    finally:
        frappe.flags.synora_memory_service_read = previous


def _lock_memory(memory_id: str) -> Any:
    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabSynora Memory Record`
        WHERE name = %s
        FOR UPDATE
        """,
        (memory_id,),
        as_dict=True,
    )
    if not rows:
        raise _not_available()
    return _load_memory(memory_id)


def _lock_memories(*memory_ids: str) -> dict[str, Any]:
    # Lock in lexical order so two concurrent corrections cannot deadlock by
    # taking predecessor/candidate locks in opposite order.
    unique_ids = sorted(set(memory_ids))
    for memory_id in unique_ids:
        _lock_memory(memory_id)
    return {memory_id: _load_memory(memory_id) for memory_id in unique_ids}


def _save_review_transition(candidate: Any, predecessor: Any | None) -> None:
    """Persist a correction approval as one rollback-safe transaction."""
    savepoint = f"synora_memory_review_{uuid4().hex}"
    frappe.db.savepoint(savepoint)
    try:
        _save_service(candidate)
        if predecessor is not None:
            _save_service(predecessor)
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise


def _dedupe_key(values: dict[str, object]) -> str:
    identity = {key: "" if value is None else str(value) for key, value in values.items()}
    payload = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _candidate_key(
    *,
    kind: str,
    initiator: str,
    company: str,
    warehouse: str | None,
    scope_run: str | None,
    source_run: str,
    source_claim_id: str | None,
    source_revision: str,
    digest: str,
    supersedes_memory: str | None,
) -> str:
    return _dedupe_key(
        {
            "kind": kind,
            "initiator": initiator,
            "company_scope": company,
            "warehouse_scope": warehouse,
            "scope_run": scope_run,
            "source_run": source_run,
            "source_claim_id": source_claim_id,
            "source_revision": source_revision,
            "digest": digest,
            "supersedes_memory": supersedes_memory,
        }
    )


def _find_by_dedupe(dedupe_key: str) -> Any | None:
    row = frappe.db.get_value(
        "Synora Memory Record",
        {"dedupe_key": dedupe_key},
        _MEMORY_FIELDS,
        as_dict=True,
    )
    if not row:
        return None
    row["doctype"] = "Synora Memory Record"
    return frappe.get_doc(row)


def _insert_candidate(values: dict[str, Any], dedupe_key: str, actor: str) -> tuple[Any, bool]:
    existing = _find_by_dedupe(dedupe_key)
    if existing is not None:
        if str(existing.dedupe_key or "") != dedupe_key:
            raise GatewayFault("CONFLICT", "memory identity conflict", 409)
        if not memory_record.can_read_memory(existing, actor):
            raise _not_available()
        return existing, False

    values["dedupe_key"] = dedupe_key
    doc = frappe.get_doc(values)
    doc.flags[SERVICE_FLAG] = True
    try:
        return doc.insert(ignore_permissions=True), True
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError) as error:
        # A concurrent request may win the unique key between the read and
        # insert.  Inspect the winner; never create a second record or retry
        # with a different identity.
        winner = _find_by_dedupe(dedupe_key)
        if winner is None:
            raise GatewayFault("CONFLICT", "memory duplicate race is unresolved", 409) from error
        if not memory_record.can_read_memory(winner, actor):
            raise _not_available() from None
        return winner, False


def create_memory_candidate(
    *,
    kind: object,
    source_run: object,
    source_revision: object,
    content: object,
    expires_at: object = None,
    source_claim_id: object = None,
) -> dict[str, Any]:
    """Create a run-bound, untrusted candidate; approval is never implicit.

    The future Coach claim resolver will become the only producer for
    persisted claim-backed content.  Until that T07 boundary exists, this
    lifecycle API accepts only an authenticated user's active Run scope and
    keeps every candidate pending and untrusted.
    """
    actor = _actor()
    safe_kind = _kind(kind)
    safe_revision = _required_text(source_revision, "source_revision", MAX_SOURCE_LENGTH)
    safe_content = _required_text(content, "content", MAX_CONTENT_LENGTH)
    safe_claim = _optional_text(source_claim_id, "source_claim_id", MAX_SOURCE_LENGTH)
    safe_expiry = _expiry(expires_at, safe_kind)
    scope = _run_scope(source_run, actor)
    safe_claim = _validated_source_claim(
        safe_claim,
        source_run=str(scope["run_id"]),
        source_revision=safe_revision,
        content=safe_content,
        scope=scope,
    )
    digest = hashlib.sha256(safe_content.encode("utf-8")).hexdigest()
    dedupe_key = _candidate_key(
        kind=safe_kind,
        initiator=str(scope["initiator"]),
        company=str(scope["company"]),
        warehouse=scope["warehouse"],
        scope_run=str(scope["run_id"]),
        source_run=str(scope["run_id"]),
        source_claim_id=safe_claim,
        source_revision=safe_revision,
        digest=digest,
        supersedes_memory=None,
    )
    values = {
        "doctype": "Synora Memory Record",
        "kind": safe_kind,
        "state": "PENDING",
        "initiator": scope["initiator"],
        "company_scope": scope["company"],
        "warehouse_scope": scope["warehouse"],
        "scope_run": scope["run_id"],
        "source_run": scope["run_id"],
        "source_claim_id": safe_claim,
        "source_revision": safe_revision,
        "content": safe_content,
        "content_classification": "UNTRUSTED",
        "digest": digest,
        "memory_version": 1,
        "state_version": 1,
        "supersedes_memory": None,
        "expires_at": safe_expiry,
    }
    memory, created = _insert_candidate(values, dedupe_key, actor)
    return {"created": created, "memory": _serialize(memory)}


def create_memory_correction(
    *,
    predecessor_memory_id: object,
    expected_predecessor_state_version: object,
    source_run: object,
    source_revision: object,
    content: object,
    expires_at: object = None,
    source_claim_id: object = None,
) -> dict[str, Any]:
    actor = _actor()
    predecessor_id = _memory_id(predecessor_memory_id)
    expected_version = _state_version(
        expected_predecessor_state_version, "expected_predecessor_state_version"
    )
    safe_revision = _required_text(source_revision, "source_revision", MAX_SOURCE_LENGTH)
    safe_content = _required_text(content, "content", MAX_CONTENT_LENGTH)
    safe_claim = _optional_text(source_claim_id, "source_claim_id", MAX_SOURCE_LENGTH)
    predecessor = _lock_memory(predecessor_id)
    if (
        not _can_manage(predecessor, actor)
        or int(predecessor.state_version) != expected_version
        or str(predecessor.state) != "APPROVED"
        or predecessor.supersedes_memory
        or _is_expired(predecessor)
    ):
        raise _not_available()
    scope = _run_scope(source_run, actor)
    if (
        str(scope["initiator"]) != str(predecessor.initiator)
        or str(scope["company"]) != str(predecessor.company_scope)
        or (scope["warehouse"] or None) != (str(predecessor.warehouse_scope or "") or None)
    ):
        raise _not_available()
    safe_claim = _validated_source_claim(
        safe_claim,
        source_run=str(scope["run_id"]),
        source_revision=safe_revision,
        content=safe_content,
        scope=scope,
    )
    safe_expiry = _expiry(expires_at, str(predecessor.kind))
    digest = hashlib.sha256(safe_content.encode("utf-8")).hexdigest()
    scope_run = str(predecessor.scope_run or "") or str(scope["run_id"])
    dedupe_key = _candidate_key(
        kind=str(predecessor.kind),
        initiator=str(predecessor.initiator),
        company=str(predecessor.company_scope),
        warehouse=str(predecessor.warehouse_scope or "") or None,
        scope_run=scope_run,
        source_run=str(scope["run_id"]),
        source_claim_id=safe_claim,
        source_revision=safe_revision,
        digest=digest,
        supersedes_memory=predecessor_id,
    )
    values = {
        "doctype": "Synora Memory Record",
        "kind": predecessor.kind,
        "state": "PENDING",
        "initiator": predecessor.initiator,
        "company_scope": predecessor.company_scope,
        "warehouse_scope": predecessor.warehouse_scope,
        "scope_run": scope_run,
        "source_run": scope["run_id"],
        "source_claim_id": safe_claim,
        "source_revision": safe_revision,
        "content": safe_content,
        "content_classification": "UNTRUSTED",
        "digest": digest,
        "memory_version": int(predecessor.memory_version) + 1,
        "state_version": 1,
        "supersedes_memory": predecessor_id,
        "expires_at": safe_expiry,
    }
    memory, created = _insert_candidate(values, dedupe_key, actor)
    return {"created": created, "memory": _serialize(memory)}


def list_review_queue(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    actor = _actor()
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > MAX_PAGE_SIZE:
        raise GatewayFault("INVALID_INPUT", "limit is invalid")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > MAX_OFFSET:
        raise GatewayFault("INVALID_INPUT", "offset is invalid")

    rows = frappe.get_all(
        "Synora Memory Record",
        filters={"state": "PENDING"},
        fields=_MEMORY_FIELDS,
        order_by="creation desc, name desc",
        limit_page_length=0,
        ignore_permissions=True,
    )
    visible = [row for row in rows if _visible_doc(row, actor)]
    selected = visible[offset : offset + limit]
    return {
        "items": [_serialize(row) for row in selected],
        "total": len(visible),
        "limit": limit,
        "offset": offset,
    }


def get_review_candidate(memory_id: object) -> dict[str, Any]:
    actor = _actor()
    safe_id = _memory_id(memory_id)
    memory = _load_memory(safe_id)
    if _is_expired(memory) or not _can_review(memory, actor):
        raise _not_available()
    if memory.state != "PENDING":
        raise _not_available()
    return _serialize_review_candidate(memory, actor)


def review_candidate(
    memory_id: object,
    decision: object,
    expected_state_version: object,
    reason: object = None,
    *,
    expected_predecessor_state_version: object = None,
) -> dict[str, Any]:
    actor = _actor()
    safe_id = _memory_id(memory_id)
    if not isinstance(decision, str) or decision not in REVIEW_DECISIONS:
        raise GatewayFault("INVALID_INPUT", "decision is invalid")
    expected_version = _state_version(expected_state_version)
    predecessor_version: int | None = None
    if expected_predecessor_state_version is not None:
        predecessor_version = _state_version(
            expected_predecessor_state_version, "expected_predecessor_state_version"
        )
    safe_reason = _reason(reason)

    predecessor_id = (
        str(frappe.db.get_value("Synora Memory Record", safe_id, "supersedes_memory") or "") or None
    )
    locked = _lock_memories(safe_id, predecessor_id) if predecessor_id else _lock_memories(safe_id)
    candidate = locked[safe_id]
    if not _can_review(candidate, actor):
        raise _not_available()
    if int(candidate.state_version) != expected_version:
        raise GatewayFault("CONFLICT", "memory review changed concurrently", 409)
    if candidate.state != "PENDING" or _is_expired(candidate):
        raise GatewayFault("CONFLICT", "memory is no longer a candidate", 409)

    predecessor: Any | None = None
    if predecessor_id:
        predecessor = locked.get(predecessor_id)
        if predecessor is None:
            raise _not_available()
        if decision == "APPROVE":
            if (
                predecessor.state != "APPROVED"
                or predecessor.supersedes_memory
                or _is_expired(predecessor)
                or str(predecessor.kind) != str(candidate.kind)
                or str(predecessor.initiator) != str(candidate.initiator)
                or str(predecessor.company_scope) != str(candidate.company_scope)
                or (str(predecessor.warehouse_scope or "") or None)
                != (str(candidate.warehouse_scope or "") or None)
                or int(candidate.memory_version) != int(predecessor.memory_version) + 1
            ):
                raise GatewayFault("CONFLICT", "predecessor changed concurrently", 409)
            if predecessor_version is None:
                raise GatewayFault(
                    "INVALID_INPUT", "expected_predecessor_state_version is required"
                )
            if int(predecessor.state_version) != predecessor_version:
                raise GatewayFault("CONFLICT", "predecessor changed concurrently", 409)
        elif (
            predecessor_version is not None
            and int(predecessor.state_version) != predecessor_version
        ):
            raise GatewayFault("CONFLICT", "predecessor changed concurrently", 409)

    if decision == "REJECT":
        candidate.state = "REJECTED"
        candidate.reviewer = actor
        candidate.reviewed_at = now_datetime()
        candidate.review_reason = safe_reason
        candidate.state_version = expected_version + 1
        candidate.flags[SERVICE_FLAG] = True
        try:
            _save_service(candidate)
        except frappe.TimestampMismatchError as error:
            raise GatewayFault("CONFLICT", "memory review changed concurrently", 409) from error
        return _serialize(candidate)

    candidate.state = "APPROVED"
    candidate.reviewer = actor
    candidate.reviewed_at = now_datetime()
    candidate.review_reason = safe_reason
    candidate.state_version = expected_version + 1
    candidate.flags[SERVICE_FLAG] = True
    if predecessor is not None:
        predecessor.state = "SUPERSEDED"
        predecessor.state_version = int(predecessor.state_version) + 1
        predecessor.flags[SERVICE_FLAG] = True
    try:
        _save_review_transition(candidate, predecessor)
    except frappe.TimestampMismatchError as error:
        raise GatewayFault("CONFLICT", "memory review changed concurrently", 409) from error
    if predecessor is None:
        return _serialize(candidate)
    return {
        "memory": _serialize(candidate),
        "superseded_memory": _serialize(predecessor),
    }


def delete_memory(
    memory_id: object, expected_state_version: object, reason: object = None
) -> dict[str, Any]:
    actor = _actor()
    safe_id = _memory_id(memory_id)
    expected_version = _state_version(expected_state_version)
    safe_reason = _reason(reason)
    memory = _lock_memory(safe_id)
    if not _can_manage(memory, actor):
        raise _not_available()
    if int(memory.state_version) != expected_version:
        raise GatewayFault("CONFLICT", "memory changed concurrently", 409)
    if memory.state == "DELETED":
        raise GatewayFault("CONFLICT", "memory is already deleted", 409)
    memory.state = "DELETED"
    # A tombstone retains identity/audit fields but clears the searchable body.
    memory.content = ""
    memory.state_version = expected_version + 1
    memory.deleted_at = now_datetime()
    memory.deleted_by = actor
    memory.deletion_reason = safe_reason
    memory.flags[SERVICE_FLAG] = True
    try:
        _save_service(memory)
    except frappe.TimestampMismatchError as error:
        raise GatewayFault("CONFLICT", "memory changed concurrently", 409) from error
    return _serialize(memory)


def _validate_recall_scope(
    company: object,
    warehouse: object,
    run_id: object,
    kind: object,
    limit: object,
    offset: object,
    actor: str,
) -> tuple[str, str | None, str | None, str | None, int, int]:
    safe_company = _required_text(company, "company", 140)
    safe_warehouse = _optional_text(warehouse, "warehouse", 140)
    safe_run: str | None = None
    if run_id is not None:
        safe_run = canonical_uuid(run_id, "run_id")
    safe_kind = None if kind is None else _kind(kind)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > MAX_PAGE_SIZE:
        raise GatewayFault("INVALID_INPUT", "limit is invalid")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > MAX_OFFSET:
        raise GatewayFault("INVALID_INPUT", "offset is invalid")
    if safe_run is not None:
        row = frappe.db.get_value(
            "Synora Agent Run",
            safe_run,
            ["initiator", "company_scope", "warehouse_scope", "status", "revoked"],
            as_dict=True,
        )
        if (
            not row
            or str(row.status) != "ACTIVE"
            or bool(row.revoked)
            or not row.initiator
            or (str(row.initiator) != actor and not _is_system_manager(actor))
            or str(row.company_scope) != safe_company
            or (str(row.warehouse_scope or "") or None) != safe_warehouse
        ):
            raise _not_available()
    return safe_company, safe_warehouse, safe_run, safe_kind, limit, offset


def list_visible_memories(
    *,
    company: object,
    warehouse: object = None,
    run_id: object = None,
    kind: object = None,
    limit: int = MAX_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    actor = _actor()
    safe_company, safe_warehouse, safe_run, safe_kind, safe_limit, safe_offset = (
        _validate_recall_scope(company, warehouse, run_id, kind, limit, offset, actor)
    )
    if not _scope_readable(safe_company, safe_warehouse, actor):
        return {
            "items": [],
            "total": 0,
            "limit": safe_limit,
            "offset": safe_offset,
        }
    filters: dict[str, object] = {"state": "APPROVED", "company_scope": safe_company}
    if safe_warehouse is not None:
        filters["warehouse_scope"] = safe_warehouse
    if safe_run is not None:
        filters["scope_run"] = safe_run
    if safe_kind is not None:
        filters["kind"] = safe_kind
    rows = frappe.get_all(
        "Synora Memory Record",
        filters=filters,
        fields=_MEMORY_FIELDS,
        order_by="creation desc, name desc",
        limit_page_length=0,
        ignore_permissions=True,
    )
    visible = [
        row
        for row in rows
        if (
            str(row.state) == "APPROVED"
            and not row.supersedes_memory
            and not _has_active_pending_correction(str(row.name))
            and not _is_expired(row)
            and row.reviewed_at
            and memory_record.can_read_memory(row, actor)
        )
    ]
    selected = visible[safe_offset : safe_offset + safe_limit]
    return {
        "items": [_serialize(row) for row in selected],
        "total": len(visible),
        "limit": safe_limit,
        "offset": safe_offset,
    }
