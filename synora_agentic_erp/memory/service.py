from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import now_datetime

from synora_agentic_erp.gateway.contract import GatewayFault, canonical_uuid
from synora_agentic_erp.synora_agentic_erp.doctype.synora_memory_record import (
    synora_memory_record as memory_record,
)

SERVICE_FLAG = memory_record.SERVICE_FLAG

MAX_PAGE_SIZE = 50
MAX_OFFSET = 10_000
MAX_REASON_LENGTH = 2_000
REVIEW_DECISIONS = frozenset({"APPROVE", "REJECT"})
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
    "memory_version",
    "state_version",
    "supersedes_memory",
    "expires_at",
    "reviewed_at",
    "reviewer",
    "review_reason",
    "creation",
]


def _actor() -> str:
    actor = str(getattr(frappe.session, "user", "Guest") or "Guest")
    if actor == "Guest":
        raise GatewayFault("AUTHENTICATION_REQUIRED", "authenticated user required", 401)
    return actor


def _not_available() -> GatewayFault:
    return GatewayFault("MEMORY_NOT_AVAILABLE", "memory is not available", 404)


def _memory_id(value: object) -> str:
    return canonical_uuid(value, "memory_id")


def _can_review(memory: Any, actor: str) -> bool:
    return memory_record.can_review_memory(memory, actor)


def _is_expired(memory: Any) -> bool:
    return memory_record.is_expired(memory)


def _visible_doc(memory: Any, actor: str) -> bool:
    return (
        str(memory.state or "") == "PENDING"
        and not memory.supersedes_memory
        and not _is_expired(memory)
        and _can_review(memory, actor)
    )


def _serialize(memory: Any) -> dict[str, Any]:
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
        "content": str(memory.content),
        "content_classification": str(memory.content_classification),
        "digest": str(memory.digest),
        "memory_version": int(memory.memory_version),
        "state_version": int(memory.state_version),
        "supersedes_memory": str(memory.supersedes_memory or "") or None,
        "expires_at": str(memory.expires_at or "") or None,
        "reviewed_at": str(memory.reviewed_at or "") or None,
        "reviewer": str(memory.reviewer or "") or None,
        "review_reason": str(memory.review_reason or "") or None,
        "created_at": str(memory.creation or ""),
    }


def _load_candidate(memory_id: str) -> Any:
    memory = _load_memory(memory_id)
    if memory.state != "PENDING":
        raise _not_available()
    return memory


def _load_memory(memory_id: str) -> Any:
    row = frappe.db.get_value(
        "Synora Memory Record",
        memory_id,
        "*",
        as_dict=True,
    )
    if not row:
        raise _not_available()
    row["doctype"] = "Synora Memory Record"
    return frappe.get_doc(row)


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
    memory = _load_candidate(safe_id)
    if memory.supersedes_memory or _is_expired(memory) or not _can_review(memory, actor):
        raise _not_available()
    return _serialize(memory)


def _reason(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_REASON_LENGTH:
        raise GatewayFault("INVALID_INPUT", "reason is invalid")
    text = value.strip()
    return text or None


def review_candidate(
    memory_id: object,
    decision: object,
    expected_state_version: object,
    reason: object = None,
) -> dict[str, Any]:
    actor = _actor()
    safe_id = _memory_id(memory_id)
    if not isinstance(decision, str) or decision not in REVIEW_DECISIONS:
        raise GatewayFault("INVALID_INPUT", "decision is invalid")
    if (
        isinstance(expected_state_version, bool)
        or not isinstance(expected_state_version, int)
        or expected_state_version < 1
    ):
        raise GatewayFault("INVALID_INPUT", "expected_state_version is invalid")
    safe_reason = _reason(reason)

    rows = frappe.db.sql(
        """
        SELECT name, state, state_version
        FROM `tabSynora Memory Record`
        WHERE name = %s
        FOR UPDATE
        """,
        (safe_id,),
        as_dict=True,
    )
    if not rows:
        raise _not_available()
    row = rows[0]
    memory = _load_memory(safe_id)
    if not _can_review(memory, actor):
        raise _not_available()
    if int(row.state_version) != expected_state_version:
        raise GatewayFault("CONFLICT", "memory review changed concurrently", 409)
    if memory.state != "PENDING":
        raise GatewayFault("CONFLICT", "memory is no longer a candidate", 409)
    if memory.supersedes_memory:
        raise GatewayFault("CONFLICT", "correction candidates are not reviewable yet", 409)
    if _is_expired(memory):
        raise GatewayFault("CONFLICT", "memory candidate has expired", 409)

    memory.state = "APPROVED" if decision == "APPROVE" else "REJECTED"
    memory.reviewer = actor
    memory.reviewed_at = now_datetime()
    memory.review_reason = safe_reason
    memory.state_version = expected_state_version + 1
    memory.flags[SERVICE_FLAG] = True
    try:
        memory.save(ignore_permissions=True)
    except frappe.TimestampMismatchError as error:
        raise GatewayFault("CONFLICT", "memory review changed concurrently", 409) from error
    return _serialize(memory)
