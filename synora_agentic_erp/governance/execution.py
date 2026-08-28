"""Frappe-side executor for the first governed Material Request Draft.

The module is intentionally target-specific.  It accepts only identifiers for
an immutable approved action; the business payload is reconstructed from the
stored action and reaches ERPNext through the normal Document controller.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import frappe
from frappe.utils import now_datetime

from synora_agentic_erp.agent.service import _set_run_state
from synora_agentic_erp.agent.state_machine import validate_transition as validate_run_transition
from synora_agentic_erp.gateway.contract import GatewayFault, canonical_uuid
from synora_agentic_erp.gateway.security import RunContext, record_gateway_audit
from synora_agentic_erp.governance.contracts import ExecutionReceipt, create_execution_receipt
from synora_agentic_erp.governance.execution_contracts import (
    ExecutionKey,
    ReadBackMismatch,
    ReconciliationClassification,
    classify_reconciliation,
    execution_key,
    map_execution_error,
    material_request_values,
    verify_material_request_read_back,
    verify_purchase_order_read_back,
)
from synora_agentic_erp.governance.policy import (
    _actor,
    _latest_approval,
    _lock_action,
    _lock_run_for_action,
    _safe_digest,
    pre_execute_recheck,
)
from synora_agentic_erp.governance.service import (
    SERVICE_FLAG,
    persist_execution_receipt,
    serialize_action,
    serialize_receipt,
    transition_action_state,
    transition_execution_receipt,
)

RESERVATION_TRANSITION_FLAG = "synora_execution_reservation_transition"
TARGET_DOCTYPE = "Material Request"
WRITER_NAME = "governed.material_request.create"
WRITER_VERSION = "1"
LEASE_SECONDS = 300
ACTION_TARGET_DOCTYPES = {
    "CREATE_MR_DRAFT": "Material Request",
    "CREATE_PO_DRAFT": "Purchase Order",
}


def _now_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_key(value: object) -> str:
    from synora_agentic_erp.governance.contracts import _idempotency_key

    return _idempotency_key(value)


def _reservation_dict(doc: Any) -> dict[str, Any]:
    return {
        "reservation_id": str(doc.reservation_id),
        "action_id": str(doc.action),
        "run_id": str(doc.run),
        "idempotency_key": str(doc.idempotency_key),
        "proposal_digest": str(doc.proposal_digest),
        "target_doctype": str(doc.target_doctype),
        "executor": str(doc.executor),
        "lease_expires_at": str(doc.lease_expires_at),
        "attempt": int(doc.attempt or 0),
        "status": str(doc.status),
        "target_name": str(doc.target_name or "") or None,
        "receipt_id": str(doc.receipt or "") or None,
        "response_category": str(doc.response_category or "") or None,
        "failure_category": str(doc.failure_category or "") or None,
        "started_at": str(doc.started_at),
        "completed_at": str(doc.completed_at or "") or None,
        "reconciliation_count": int(doc.reconciliation_count or 0),
        "last_reconciled_at": str(doc.last_reconciled_at or "") or None,
        "correlation_id": str(doc.correlation_id),
    }


def _reservation_by_key(key: str, *, lock: bool = False) -> Any | None:
    suffix = " FOR UPDATE" if lock else ""
    rows = frappe.db.sql(
        f"""
        SELECT name
        FROM `tabSynora Execution Reservation`
        WHERE idempotency_key = %s
        {suffix}
        """,
        (key,),
        as_dict=True,
    )
    return frappe.get_doc("Synora Execution Reservation", rows[0].name) if rows else None


def _reservation_identity_matches(doc: Any, action: Any, key: ExecutionKey, actor: str) -> None:
    if (
        str(doc.action) != action.action_id
        or str(doc.run) != action.run_id
        or str(doc.idempotency_key) != key.idempotency_key
        or str(doc.proposal_digest) != key.proposal_digest
        or str(doc.target_doctype) != key.target_doctype
        or str(doc.executor) != actor
    ):
        raise GatewayFault("CONFLICT", "idempotency reservation conflicts", 409)


def _insert_reservation(action: Any, key: ExecutionKey, actor: str) -> tuple[Any, bool]:
    existing = _reservation_by_key(key.idempotency_key, lock=True)
    if existing is not None:
        _reservation_identity_matches(existing, action, key, actor)
        return existing, False
    doc = frappe.get_doc(
        {
            "doctype": "Synora Execution Reservation",
            "reservation_id": str(uuid4()),
            "action": action.action_id,
            "run": action.run_id,
            "idempotency_key": key.idempotency_key,
            "proposal_digest": key.proposal_digest,
            "target_doctype": key.target_doctype,
            "executor": actor,
            "owner_token": str(uuid4()),
            "lease_expires_at": (datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "attempt": 1,
            "status": "STARTED",
            "started_at": _now_timestamp(),
            "correlation_id": action.correlation_id,
        }
    )
    doc.flags[SERVICE_FLAG] = True
    try:
        doc.insert(ignore_permissions=True)
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError) as error:
        # A concurrent request may have inserted the unique key between the
        # read and insert.  This transaction has not called the ERP controller;
        # discard local state and inspect the winner instead.
        frappe.db.rollback()
        winner = _reservation_by_key(key.idempotency_key, lock=True)
        if winner is None:
            raise GatewayFault(
                "CONFLICT", "idempotency reservation race is unresolved", 409
            ) from error
        _reservation_identity_matches(winner, action, key, actor)
        return winner, False
    return doc, True


def _update_reservation(
    doc: Any,
    status: str,
    *,
    target_name: str | None = None,
    receipt_id: str | None = None,
    response_category: str | None = None,
    failure_category: str | None = None,
) -> Any:
    if status not in {
        "SUCCEEDED",
        "FAILED",
        "RECONCILIATION_REQUIRED",
        "RECONCILED_SUCCESS",
        "RECONCILED_FAILURE",
        "MANUAL_INTERVENTION",
    }:
        raise GatewayFault("INVALID_INPUT", "execution reservation status is invalid")
    current_status = str(doc.status)
    if current_status not in {"STARTED", "RECONCILIATION_REQUIRED"} and current_status != status:
        raise GatewayFault("CONFLICT", "execution reservation is already finalized", 409)
    if current_status == status and all(
        value is None
        for value in (
            target_name,
            receipt_id,
            response_category,
            failure_category,
        )
    ):
        return doc
    if current_status != status:
        doc.status = status
    if target_name is not None:
        doc.target_name = target_name
    if receipt_id is not None:
        doc.receipt = receipt_id
    if response_category is not None:
        doc.response_category = response_category
    if failure_category is not None:
        doc.failure_category = failure_category
    if current_status != status and status in {
        "RECONCILED_SUCCESS",
        "RECONCILED_FAILURE",
        "MANUAL_INTERVENTION",
    }:
        doc.reconciliation_count = int(doc.reconciliation_count or 0) + 1
        doc.last_reconciled_at = _now_timestamp()
    doc.completed_at = _now_timestamp()
    doc.flags[SERVICE_FLAG] = True
    doc.flags[RESERVATION_TRANSITION_FLAG] = True
    doc.save(ignore_permissions=True)
    return doc


def _run_context(run: Any) -> RunContext:
    return RunContext(
        run_id=str(run.name),
        initiator=str(run.initiator),
        company=str(run.company_scope),
        warehouse=str(run.warehouse_scope or "") or None,
        state_version=int(run.state_version),
    )


def _load_readable_target(action: Any, target_name: str, actor: str) -> Any:
    """Load one ERP target only after rechecking the actor's row-level scope.

    ``frappe.get_doc`` is intentionally called only after an actor-scoped
    ``get_list`` proves that the named parent is visible.  The parent query is
    not enough for a governed action: company, warehouse, supplier and an
    optional Material Request prerequisite are all rechecked through the
    current session's permission query conditions before a cached result or a
    reconciliation match can be returned.
    """

    target_doctype = ACTION_TARGET_DOCTYPES.get(str(action.action_type))
    if target_doctype is None:
        raise GatewayFault("INVALID_INPUT", "unsupported governed target", 400)
    payload = action.payload
    if not frappe.has_permission(target_doctype, "read", user=actor):
        raise GatewayFault("PERMISSION_DENIED", "current ERP permission is insufficient", 403)

    parent_rows = frappe.get_list(
        target_doctype,
        filters={"name": target_name, "company": payload["company"], "docstatus": 0},
        fields=["name"],
        user=actor,
        limit=1,
    )
    if not parent_rows:
        raise GatewayFault("PERMISSION_DENIED", "target is outside current ERP scope", 403)

    company_rows = frappe.get_list(
        "Company",
        pluck="name",
        filters={"name": payload["company"]},
        user=actor,
        limit=1,
    )
    if not company_rows:
        raise GatewayFault("PERMISSION_DENIED", "target company is outside current ERP scope", 403)

    warehouses = sorted({str(item["warehouse"]) for item in payload["items"]})
    for warehouse in warehouses:
        warehouse_rows = frappe.get_list(
            "Warehouse",
            pluck="name",
            filters={
                "name": warehouse,
                "company": payload["company"],
                "disabled": 0,
            },
            user=actor,
            limit=1,
        )
        if not warehouse_rows:
            raise GatewayFault(
                "PERMISSION_DENIED", "target warehouse is outside current ERP scope", 403
            )

    if target_doctype == "Purchase Order":
        supplier_rows = frappe.get_list(
            "Supplier",
            pluck="name",
            filters={"name": payload["supplier"], "disabled": 0},
            user=actor,
            limit=1,
        )
        if not supplier_rows:
            raise GatewayFault(
                "PERMISSION_DENIED", "target supplier is outside current ERP scope", 403
            )
        for item in payload["items"]:
            material_request = item.get("material_request")
            if material_request:
                prerequisite_rows = frappe.get_list(
                    "Material Request",
                    pluck="name",
                    filters={
                        "name": material_request,
                        "company": payload["company"],
                        "docstatus": ["<", 2],
                    },
                    user=actor,
                    limit=1,
                )
                if not prerequisite_rows:
                    raise GatewayFault(
                        "PERMISSION_DENIED",
                        "material request prerequisite is outside current ERP scope",
                        403,
                    )

    try:
        return frappe.get_doc(target_doctype, target_name)
    except frappe.DoesNotExistError as error:
        raise GatewayFault(
            "UNCERTAIN_RESULT", "verified ERP outcome is unavailable", 503
        ) from error


def _serialize_receipt_for_actor(
    action: Any,
    reservation: Any | None,
    receipt_doc: Any,
    actor: str,
    verifier: Callable[[Any, Any], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Serialize a Receipt only after rechecking its target for this actor.

    Governance records are intentionally loaded with ignore_permissions by
    their owner-scoped API paths. A Receipt that contains an ERP target is
    different: its target name and verified fields are cached business facts,
    so returning it requires a fresh actor-scoped ERP row query and read-back.
    The reservation target is checked as well, preventing a stale reservation
    field from bypassing the same gate.
    """

    reservation_target_name = str(getattr(reservation, "target_name", "") or "")
    receipt_target_name = str(getattr(receipt_doc, "target_name", "") or "")
    receipt_target_doctype = str(getattr(receipt_doc, "target_doctype", "") or "")
    # Every reservation has a target_doctype before a writer runs; do not treat
    # that non-sensitive discriminator as proof that a target exists.
    has_cached_target = bool(
        reservation_target_name or receipt_target_name or receipt_target_doctype
    )
    if has_cached_target:
        expected_doctype = ACTION_TARGET_DOCTYPES.get(str(action.action_type))
        if expected_doctype is None:
            raise GatewayFault("UNCERTAIN_RESULT", "verified Receipt target is invalid", 503)
        if not receipt_target_name or receipt_target_doctype != expected_doctype:
            raise GatewayFault("UNCERTAIN_RESULT", "verified Receipt target is incomplete", 503)
        if reservation_target_name and reservation_target_name != receipt_target_name:
            raise GatewayFault(
                "UNCERTAIN_RESULT",
                "verified Receipt conflicts with reservation",
                503,
            )
        if verifier is None:
            if expected_doctype == TARGET_DOCTYPE:
                verifier = verify_material_request_read_back
            elif expected_doctype == "Purchase Order":
                verifier = verify_purchase_order_read_back
            else:  # pragma: no cover - ACTION_TARGET_DOCTYPES is closed above.
                raise GatewayFault("UNCERTAIN_RESULT", "verified Receipt target is invalid", 503)
        try:
            target = _load_readable_target(action, receipt_target_name, actor)
            verified = verifier(action, target)
        except ReadBackMismatch as error:
            raise GatewayFault(
                "UNCERTAIN_RESULT", "ERP read-back no longer matches Receipt", 503
            ) from error
        try:
            recorded = json.loads(receipt_doc.verified_fields_json)
        except (TypeError, ValueError) as error:
            raise GatewayFault(
                "UNCERTAIN_RESULT", "verified Receipt evidence is invalid", 503
            ) from error
        if recorded != verified:
            raise GatewayFault("UNCERTAIN_RESULT", "ERP read-back no longer matches Receipt", 503)
    try:
        return serialize_receipt(receipt_doc)
    except (TypeError, ValueError) as error:
        raise GatewayFault(
            "UNCERTAIN_RESULT", "verified Receipt evidence is invalid", 503
        ) from error


def _move_run_to_executing(run: Any) -> Any:
    current = str(run.run_state)
    if current == "PROPOSED":
        validate_run_transition(current, "AWAITING_APPROVAL")
        _set_run_state(run, "AWAITING_APPROVAL")
        current = str(run.run_state)
    if current != "AWAITING_APPROVAL":
        raise GatewayFault("CONFLICT", "Run is not ready for governed execution", 409)
    validate_run_transition(current, "EXECUTING")
    _set_run_state(run, "EXECUTING")
    return run


def _close_run(run: Any, target: str, correlation_id: str) -> Any:
    if target not in {"SUCCEEDED", "FAILED", "RECONCILIATION_REQUIRED"}:
        raise GatewayFault("INVALID_INPUT", "Run terminal state is invalid")
    if str(run.run_state) != target:
        validate_run_transition(str(run.run_state), target)
        _set_run_state(run, target)
    run.flags.synora_revocation = True
    run.revoked = 1
    run.status = "REVOKED"
    run.revoked_at = now_datetime()
    run.revoked_by = frappe.session.user
    run.revocation_correlation_id = correlation_id
    run.save(ignore_permissions=True)
    return run


def _receipt_input(
    action: Any,
    run: Any,
    reservation: Any,
    *,
    final_state: str,
    response_category: str,
    failure_category: str | None,
    target_name: str | None,
    verified_fields: dict[str, Any],
    reconciliation_evidence: dict[str, Any] | None = None,
) -> ExecutionReceipt:
    approval = _latest_approval(action.action_id, action.proposal_digest, "ALLOW")
    approver = str(approval.actor) if approval is not None else None
    return create_execution_receipt(
        {
            "receipt_id": str(uuid4()),
            "action_id": action.action_id,
            "run_id": run.name,
            "idempotency_key": action.idempotency_key,
            "initiator": action.initiator,
            "approver": approver,
            "executor": str(reservation.executor),
            "proposal_digest": action.proposal_digest,
            "target_doctype": ACTION_TARGET_DOCTYPES[action.action_type] if target_name else None,
            "target_name": target_name,
            "verified_fields": verified_fields,
            "response_category": response_category,
            "failure_category": failure_category,
            "final_state": final_state,
            "started_at": str(reservation.started_at),
            "completed_at": _now_timestamp(),
            "correlation_id": str(reservation.correlation_id),
            "reconciliation_evidence": reconciliation_evidence,
        }
    )


def _persist_receipt(receipt: ExecutionReceipt) -> dict[str, Any]:
    return persist_execution_receipt(
        receipt,
        verified_execution=receipt.final_state in {"SUCCEEDED", "RECONCILED_SUCCESS"},
    )


def _audit(run: Any, correlation_id: str, outcome: str, error_code: str | None = None) -> None:
    record_gateway_audit(
        _run_context(run),
        WRITER_NAME,
        WRITER_VERSION,
        correlation_id,
        outcome,
        error_code,
    )


def _success_response(
    action_doc: Any,
    run: Any,
    reservation: Any,
    receipt: dict[str, Any],
    target_name: str,
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": "1",
        "correlation_id": str(reservation.correlation_id),
        "run": {
            "run_id": str(run.name),
            "run_state": str(run.run_state),
            "state_version": int(run.state_version),
        },
        "action": serialize_action(action_doc),
        "reservation": _reservation_dict(reservation),
        "target": {"doctype": TARGET_DOCTYPE, "name": target_name, "docstatus": 0},
        "receipt": receipt,
    }


def _replay_success(
    action_doc: Any,
    action: Any,
    run: Any,
    reservation: Any,
    actor: str,
) -> dict[str, Any]:
    """Recheck current read permission and return the already verified result."""

    _reservation_identity_matches(
        reservation,
        action,
        execution_key(action),
        actor,
    )
    if str(action_doc.state) != "EXECUTED" or str(reservation.status) not in {
        "SUCCEEDED",
        "RECONCILED_SUCCESS",
    }:
        raise GatewayFault("CONFLICT", "execution reservation is not replayable", 409)
    if not frappe.db.get_value("User", actor, "enabled"):
        raise GatewayFault("PERMISSION_DENIED", "current user is disabled", 403)
    if not frappe.has_permission(TARGET_DOCTYPE, "read", user=actor):
        raise GatewayFault("PERMISSION_DENIED", "current ERP permission is insufficient", 403)
    receipt_name = str(reservation.receipt or "")
    target_name = str(reservation.target_name or "")
    if not receipt_name or not target_name:
        raise GatewayFault("UNCERTAIN_RESULT", "verified replay evidence is incomplete", 503)
    try:
        receipt_doc = frappe.get_doc("Synora Execution Receipt", receipt_name)
    except frappe.DoesNotExistError as error:
        raise GatewayFault(
            "UNCERTAIN_RESULT", "verified ERP outcome is unavailable", 503
        ) from error
    if (
        receipt_doc.final_state not in {"SUCCEEDED", "RECONCILED_SUCCESS"}
        or receipt_doc.target_name != target_name
    ):
        raise GatewayFault("UNCERTAIN_RESULT", "verified Receipt conflicts with target", 503)
    serialized_receipt = _serialize_receipt_for_actor(
        action,
        reservation,
        receipt_doc,
        actor,
    )
    _audit(run, str(reservation.correlation_id), "CACHED")
    frappe.db.commit()
    return _success_response(
        action_doc,
        run,
        reservation,
        serialized_receipt,
        target_name,
    )


def _finalize_failure(
    action_id: str,
    reservation_id: str,
    *,
    category: str,
    failure_category: str,
    reason: str,
    uncertain: bool,
) -> None:
    """Finalize a post-reservation failure in a new transaction."""

    try:
        reservation = frappe.get_doc("Synora Execution Reservation", reservation_id)
        action_doc = frappe.get_doc("Synora Proposed Action", action_id)
        run = frappe.get_doc("Synora Agent Run", reservation.run)
        action = _load_action_from_doc(action_doc)
        if str(reservation.status) != "STARTED":
            return
        final_state = "RECONCILIATION_REQUIRED" if uncertain else "FAILED"
        receipt = _receipt_input(
            action,
            run,
            reservation,
            final_state=final_state,
            response_category="UNCERTAIN_RESULT" if uncertain else category,
            failure_category=failure_category,
            target_name=None,
            verified_fields={},
            reconciliation_evidence={"reason": reason} if uncertain else None,
        )
        stored_receipt = _persist_receipt(receipt)
        if not uncertain and str(action_doc.state) == "APPROVED":
            transition_action_state(
                action_id,
                "EXPIRED",
                expected_version=int(action_doc.state_version),
                reason=reason[:2_000],
                correlation_id=str(reservation.correlation_id),
            )
        _update_reservation(
            reservation,
            "RECONCILIATION_REQUIRED" if uncertain else "FAILED",
            receipt_id=str(stored_receipt["receipt_id"]),
            response_category="UNCERTAIN_RESULT" if uncertain else category,
            failure_category=failure_category,
        )
        _close_run(
            run,
            "RECONCILIATION_REQUIRED" if uncertain else "FAILED",
            str(reservation.correlation_id),
        )
        _audit(run, str(reservation.correlation_id), "REJECTED", failure_category)
        frappe.db.commit()
    except Exception:
        # The original reservation remains STARTED if this recovery transaction
        # cannot commit.  That is intentionally visible to the future
        # reconciliation worker; no retry is attempted here.
        try:
            frappe.db.rollback()
        except Exception:
            pass


def _load_action_from_doc(doc: Any) -> Any:
    from synora_agentic_erp.governance.contracts import build_proposed_action

    return build_proposed_action(
        {
            "schema_version": doc.schema_version,
            "action_type": doc.action_type,
            "run_id": doc.run,
            "action_id": doc.action_id,
            "initiator": doc.initiator,
            "payload": json.loads(doc.payload_json),
            "evidence_refs": json.loads(doc.evidence_refs_json),
            "calculation_refs": json.loads(doc.calculation_refs_json),
            "risk_class": doc.risk_class,
            "approval_class": doc.approval_class,
            "snapshot_ref": doc.snapshot_ref,
            "idempotency_key": doc.idempotency_key,
            "expires_at": doc.expires_at,
            "revalidation_rule": doc.revalidation_rule,
            "proposal_digest": doc.proposal_digest,
            "summary": doc.summary or "",
            "correlation_id": doc.correlation_id,
        }
    )


def _lease_expired(reservation: Any) -> bool:
    raw = str(reservation.lease_expires_at or "")
    if not raw:
        return False
    try:
        expiry = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return expiry <= datetime.now(UTC)


def _reconciliation_candidates(
    action: Any,
    reservation: Any,
    actor: str,
) -> tuple[list[str], list[tuple[str, dict[str, Any]]]]:
    target_name = str(reservation.target_name or "")
    if target_name:
        names = [target_name]
    else:
        payload = action.payload
        item_codes = sorted({str(item["item_code"]) for item in payload["items"]})
        warehouses = sorted({str(item["warehouse"]) for item in payload["items"]})
        child_rows = frappe.get_list(
            "Material Request Item",
            filters={
                "item_code": ["in", item_codes],
                "warehouse": ["in", warehouses],
            },
            fields=["parent"],
            parent_doctype=TARGET_DOCTYPE,
            order_by="parent asc",
            limit=100,
            user=actor,
        )
        parents = sorted({str(row.parent) for row in child_rows})
        if not parents:
            return [], []
        rows = frappe.get_list(
            TARGET_DOCTYPE,
            filters={
                "name": ["in", parents],
                "company": payload["company"],
                "material_request_type": payload["material_request_type"],
                "transaction_date": payload["transaction_date"],
                "docstatus": 0,
            },
            fields=["name"],
            order_by="name asc",
            limit=50,
            user=actor,
        )
        names = [str(row.name) for row in rows]
    matches: list[tuple[str, dict[str, Any]]] = []
    for name in names:
        try:
            target = _load_readable_target(action, name, actor)
            matches.append((name, verify_material_request_read_back(action, target)))
        except ReadBackMismatch:
            continue
        except frappe.DoesNotExistError:
            continue
    return names, matches


def _reconciliation_evidence(
    classification: ReconciliationClassification,
    candidate_names: list[str],
    matches: list[tuple[str, dict[str, Any]]],
    correlation_id: str,
) -> dict[str, Any]:
    return {
        "result_status": classification.result_status,
        "reason": classification.reason,
        "candidate_count": len(candidate_names),
        "matching_count": len(matches),
        "candidate_names": ",".join(candidate_names[:20]),
        "matching_names": ",".join(name for name, _verified in matches[:20]),
        "reconciliation_correlation_id": correlation_id,
    }


def _reconciliation_response(
    action_doc: Any,
    run: Any,
    reservation: Any,
    receipt_doc: Any | None,
    *,
    result_status: str,
    evidence: dict[str, Any],
    correlation_id: str,
) -> dict[str, Any]:
    action = _load_action_from_doc(action_doc)
    reservation_target_name = str(getattr(reservation, "target_name", "") or "")
    if receipt_doc is None and reservation_target_name:
        raise GatewayFault("UNCERTAIN_RESULT", "verified Receipt evidence is incomplete", 503)
    serialized_receipt = (
        _serialize_receipt_for_actor(
            action,
            reservation,
            receipt_doc,
            str(getattr(frappe.session, "user", "Guest") or "Guest"),
        )
        if receipt_doc is not None
        else None
    )
    return {
        "ok": True,
        "schema_version": "1",
        "result_status": result_status,
        "can_retry": False,
        "correlation_id": correlation_id,
        "run": {
            "run_id": str(run.name),
            "run_state": str(run.run_state),
            "state_version": int(run.state_version),
        },
        "action": serialize_action(action_doc),
        "reservation": _reservation_dict(reservation),
        "receipt": serialized_receipt,
        "target": (
            {
                "doctype": TARGET_DOCTYPE,
                "name": str(receipt_doc.target_name),
                "docstatus": 0,
            }
            if receipt_doc is not None and receipt_doc.target_name
            else None
        ),
        "reconciliation": evidence,
    }


def _reconciliation_receipt(
    action: Any,
    run: Any,
    reservation: Any,
    existing: Any | None,
    *,
    final_state: str,
    response_category: str,
    failure_category: str | None,
    target_name: str | None,
    verified_fields: dict[str, Any],
    evidence: dict[str, Any],
) -> ExecutionReceipt:
    approval = _latest_approval(action.action_id, action.proposal_digest, "ALLOW")
    return create_execution_receipt(
        {
            "receipt_id": str(existing.receipt_id) if existing is not None else str(uuid4()),
            "action_id": action.action_id,
            "run_id": run.name,
            "idempotency_key": action.idempotency_key,
            "initiator": action.initiator,
            "approver": (
                str(existing.approver)
                if existing is not None and existing.approver
                else str(approval.actor)
                if approval is not None
                else None
            ),
            "executor": str(reservation.executor),
            "proposal_digest": action.proposal_digest,
            "target_doctype": TARGET_DOCTYPE if target_name else None,
            "target_name": target_name,
            "verified_fields": verified_fields,
            "response_category": response_category,
            "failure_category": failure_category,
            "final_state": final_state,
            "started_at": str(existing.started_at)
            if existing is not None
            else str(reservation.started_at),
            "completed_at": _now_timestamp(),
            "correlation_id": str(existing.correlation_id)
            if existing is not None
            else str(reservation.correlation_id),
            "reconciliation_evidence": evidence,
        }
    )


def reconcile_material_request(
    action_id: object,
    expected_digest: object,
    idempotency_key: object,
    correlation_id: object,
) -> dict[str, Any]:
    """Read ERP state and close one uncertain MR reservation without writing ERP."""

    safe_action_id = canonical_uuid(action_id, "action_id")
    safe_digest = _safe_digest(expected_digest)
    safe_key = _safe_key(idempotency_key)
    safe_correlation = canonical_uuid(correlation_id, "correlation_id")
    actor = _actor()
    run = _lock_run_for_action(safe_action_id)
    action_doc, action, locked = _lock_action(safe_action_id)
    if action.run_id != run.name or action.proposal_digest != safe_digest:
        raise GatewayFault("CONFLICT", "reconciliation digest or Run conflicts", 409)
    if action.idempotency_key != safe_key or action.action_type != "CREATE_MR_DRAFT":
        raise GatewayFault("CONFLICT", "reconciliation idempotency tuple conflicts", 409)
    key = execution_key(action)
    reservation = _reservation_by_key(safe_key, lock=True)
    if reservation is None:
        raise GatewayFault("NOT_FOUND", "execution reservation is not available", 404)
    _reservation_identity_matches(reservation, action, key, actor)
    if not frappe.db.get_value("User", actor, "enabled"):
        raise GatewayFault("PERMISSION_DENIED", "current user is disabled", 403)
    if not frappe.has_permission(TARGET_DOCTYPE, "read", user=actor):
        raise GatewayFault("PERMISSION_DENIED", "current ERP permission is insufficient", 403)

    receipt_doc = None
    if reservation.receipt:
        try:
            receipt_doc = frappe.get_doc("Synora Execution Receipt", reservation.receipt)
        except frappe.DoesNotExistError as error:
            raise GatewayFault(
                "UNCERTAIN_RESULT", "reconciliation Receipt is unavailable", 503
            ) from error

    current_status = str(reservation.status)
    if current_status in {"SUCCEEDED", "RECONCILED_SUCCESS"}:
        return _reconciliation_response(
            action_doc,
            run,
            reservation,
            receipt_doc,
            result_status="RECONCILED_SUCCESS"
            if current_status == "RECONCILED_SUCCESS"
            else "SUCCEEDED",
            evidence={"reason": "verified execution already finalized", "candidate_count": 1},
            correlation_id=safe_correlation,
        )
    if current_status in {"FAILED", "RECONCILED_FAILURE", "MANUAL_INTERVENTION"}:
        return _reconciliation_response(
            action_doc,
            run,
            reservation,
            receipt_doc,
            result_status=(
                str(receipt_doc.final_state) if receipt_doc is not None else current_status
            ),
            evidence={"reason": "execution is already finalized", "candidate_count": 0},
            correlation_id=safe_correlation,
        )
    if current_status not in {"STARTED", "RECONCILIATION_REQUIRED"}:
        raise GatewayFault("CONFLICT", "execution reservation status is invalid", 409)

    if not _lease_expired(reservation):
        evidence = {
            "result_status": "RECONCILIATION_REQUIRED",
            "reason": "execution lease is still active",
            "candidate_count": 0,
            "matching_count": 0,
            "next_reconcile_at": str(reservation.lease_expires_at),
            "reconciliation_correlation_id": safe_correlation,
        }
        return _reconciliation_response(
            action_doc,
            run,
            reservation,
            receipt_doc,
            result_status="RECONCILIATION_REQUIRED",
            evidence=evidence,
            correlation_id=safe_correlation,
        )

    candidate_names, matches = _reconciliation_candidates(action, reservation, actor)
    classification = classify_reconciliation(
        candidate_count=len(candidate_names),
        matching_count=len(matches),
        lease_expired=True,
        failure_evidence_complete=bool(reservation.failure_category),
    )
    evidence = _reconciliation_evidence(
        classification,
        candidate_names,
        matches,
        safe_correlation,
    )
    if current_status == "STARTED":
        _update_reservation(
            reservation,
            "RECONCILIATION_REQUIRED",
            response_category="UNCERTAIN_RESULT",
            failure_category=str(reservation.failure_category or "RECONCILIATION_PENDING"),
        )
        reservation = frappe.get_doc("Synora Execution Reservation", reservation.name)

    if classification.result_status == "RECONCILED_SUCCESS" and str(action_doc.state) != "APPROVED":
        classification = ReconciliationClassification(
            "MANUAL_INTERVENTION",
            "Action state is no longer APPROVED",
        )
        evidence = _reconciliation_evidence(
            classification,
            candidate_names,
            matches,
            safe_correlation,
        )

    target_name = matches[0][0] if classification.result_status == "RECONCILED_SUCCESS" else None
    verified = matches[0][1] if target_name else {}
    response_category = (
        "ERP_SUCCESS"
        if classification.result_status == "RECONCILED_SUCCESS"
        else str(reservation.response_category or "UNCERTAIN_RESULT")
    )
    failure_category = (
        None
        if classification.result_status == "RECONCILED_SUCCESS"
        else str(reservation.failure_category or classification.result_status)
    )
    final_state = classification.result_status
    receipt = _reconciliation_receipt(
        action,
        run,
        reservation,
        receipt_doc,
        final_state=final_state,
        response_category=response_category,
        failure_category=failure_category,
        target_name=target_name,
        verified_fields=verified,
        evidence=evidence,
    )
    if receipt_doc is None:
        stored_receipt = _persist_receipt(receipt)
        receipt_doc = frappe.get_doc("Synora Execution Receipt", stored_receipt["receipt_id"])
    else:
        stored_receipt = transition_execution_receipt(receipt)
        receipt_doc = frappe.get_doc("Synora Execution Receipt", stored_receipt["receipt_id"])

    if classification.result_status == "RECONCILED_SUCCESS":
        transition_action_state(
            action.action_id,
            "EXECUTED",
            expected_version=int(locked["state_version"]),
            reason="ERP Draft found by read-only reconciliation",
            correlation_id=safe_correlation,
            approval_digest=action.proposal_digest,
        )
        _update_reservation(
            reservation,
            "RECONCILED_SUCCESS",
            target_name=target_name,
            receipt_id=str(stored_receipt["receipt_id"]),
            response_category="ERP_SUCCESS",
        )
        _close_run(run, "SUCCEEDED", safe_correlation)
        _audit(run, safe_correlation, "CACHED", "RECONCILED_SUCCESS")
    else:
        if str(action_doc.state) == "APPROVED":
            transition_action_state(
                action.action_id,
                "EXPIRED",
                expected_version=int(locked["state_version"]),
                reason=classification.reason[:2_000],
                correlation_id=safe_correlation,
            )
        _update_reservation(
            reservation,
            classification.result_status,
            receipt_id=str(stored_receipt["receipt_id"]),
            response_category=response_category,
            failure_category=failure_category,
        )
        if str(run.run_state) == "EXECUTING":
            _set_run_state(run, "RECONCILIATION_REQUIRED")
        if classification.result_status == "RECONCILED_FAILURE":
            _close_run(run, "FAILED", safe_correlation)
        else:
            _close_run(run, "RECONCILIATION_REQUIRED", safe_correlation)
        _audit(run, safe_correlation, "REJECTED", failure_category)
    frappe.db.commit()
    action_doc = frappe.get_doc("Synora Proposed Action", action.action_id)
    return _reconciliation_response(
        action_doc,
        run,
        reservation,
        receipt_doc,
        result_status=classification.result_status,
        evidence=evidence,
        correlation_id=safe_correlation,
    )


def execute_material_request(
    action_id: object,
    expected_digest: object,
    idempotency_key: object,
    correlation_id: object,
) -> dict[str, Any]:
    """Execute one approved MR Draft, or return its verified idempotent result."""

    safe_action_id = canonical_uuid(action_id, "action_id")
    safe_digest = _safe_digest(expected_digest)
    safe_key = _safe_key(idempotency_key)
    safe_correlation = canonical_uuid(correlation_id, "correlation_id")
    actor = _actor()

    run = _lock_run_for_action(safe_action_id)
    action_doc, action, locked = _lock_action(safe_action_id)
    if action.run_id != run.name or action.proposal_digest != safe_digest:
        raise GatewayFault("CONFLICT", "execution digest or Run conflicts", 409)
    if action.idempotency_key != safe_key:
        raise GatewayFault("CONFLICT", "idempotency key conflicts", 409)
    if action.action_type != "CREATE_MR_DRAFT":
        raise GatewayFault("INVALID_INPUT", "only CREATE_MR_DRAFT is supported", 400)
    key = execution_key(action)
    if key.proposal_digest != safe_digest or key.idempotency_key != safe_key:
        raise GatewayFault("CONFLICT", "execution key conflicts", 409)

    existing = _reservation_by_key(safe_key, lock=True)
    if existing is not None:
        _reservation_identity_matches(existing, action, key, actor)
        if str(existing.status) in {"SUCCEEDED", "RECONCILED_SUCCESS"}:
            return _replay_success(action_doc, action, run, existing, actor)
        if str(existing.status) == "RECONCILIATION_REQUIRED":
            raise GatewayFault("UNCERTAIN_RESULT", "execution result requires reconciliation", 503)
        if str(existing.status) in {
            "FAILED",
            "RECONCILED_FAILURE",
            "MANUAL_INTERVENTION",
        }:
            raise GatewayFault("CONFLICT", "execution is finalized and cannot be retried", 409)
        raise GatewayFault("CONFLICT", "execution is already reserved and cannot be retried", 409)

    # The first recheck is made before the reservation.  This prevents a
    # caller from reserving a key for an action that is already stale.
    pre_execute_recheck(safe_action_id, safe_digest, safe_key)
    run = _lock_run_for_action(safe_action_id)
    action_doc, action, locked = _lock_action(safe_action_id)
    if str(action_doc.state) != "APPROVED":
        raise GatewayFault("CONFLICT", "governed action is not approved", 409)
    reservation, created = _insert_reservation(action, key, actor)
    if not created:
        if str(reservation.status) in {"SUCCEEDED", "RECONCILED_SUCCESS"}:
            frappe.db.rollback()
            run = _lock_run_for_action(safe_action_id)
            action_doc, action, _ = _lock_action(safe_action_id)
            return _replay_success(action_doc, action, run, reservation, actor)
        if str(reservation.status) in {
            "FAILED",
            "RECONCILED_FAILURE",
            "MANUAL_INTERVENTION",
        }:
            raise GatewayFault("CONFLICT", "execution is finalized and cannot be retried", 409)
        raise GatewayFault("CONFLICT", "execution is already reserved and cannot be retried", 409)
    reservation_id = str(reservation.reservation_id)
    try:
        _move_run_to_executing(run)
        frappe.db.commit()
    except Exception as error:
        frappe.db.rollback()
        raise GatewayFault(
            "CONFLICT", "execution reservation could not be committed", 409
        ) from error

    try:
        # T2 is a fresh transaction after the durable STARTED reservation.
        pre_execute_recheck(safe_action_id, safe_digest, safe_key)
        run = _lock_run_for_action(safe_action_id)
        action_doc, action, locked = _lock_action(safe_action_id)
        values = material_request_values(action)
        target = frappe.get_doc(values)
        target.insert()
        target = frappe.get_doc(TARGET_DOCTYPE, target.name)
        verified = verify_material_request_read_back(action, target)
        receipt = _receipt_input(
            action,
            run,
            reservation,
            final_state="SUCCEEDED",
            response_category="ERP_SUCCESS",
            failure_category=None,
            target_name=str(target.name),
            verified_fields=verified,
        )
        stored_receipt = _persist_receipt(receipt)
        transition_action_state(
            action.action_id,
            "EXECUTED",
            expected_version=int(locked["state_version"]),
            reason="Material Request Draft created and read back",
            correlation_id=safe_correlation,
            approval_digest=action.proposal_digest,
        )
        _update_reservation(
            reservation,
            "SUCCEEDED",
            target_name=str(target.name),
            receipt_id=str(stored_receipt["receipt_id"]),
            response_category="ERP_SUCCESS",
        )
        _close_run(run, "SUCCEEDED", safe_correlation)
        _audit(run, safe_correlation, "SUCCEEDED")
        frappe.db.commit()
        stored_action_doc = frappe.get_doc("Synora Proposed Action", action.action_id)
        return _success_response(
            stored_action_doc,
            run,
            reservation,
            stored_receipt,
            str(target.name),
        )
    except Exception as error:
        category, failure_category, status = map_execution_error(error)
        try:
            frappe.db.rollback()
        except Exception:
            pass
        uncertain = category == "UNCERTAIN_RESULT"
        _finalize_failure(
            safe_action_id,
            reservation_id,
            category=category,
            failure_category=failure_category,
            reason=failure_category,
            uncertain=uncertain,
        )
        raise GatewayFault(
            category,
            "governed Material Request execution failed",
            status,
        ) from error


__all__ = ["execute_material_request", "reconcile_material_request"]
