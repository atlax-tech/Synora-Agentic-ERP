"""Explicit governed Purchase Order Draft writer and reconciliation handler.

The module deliberately accepts only the immutable action tuple.  It never
submits a Purchase Order and never converts an existing Material Request;
ERPNext's normal Purchase Order controller remains the only business write
path.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import frappe

from synora_agentic_erp.agent.service import _set_run_state
from synora_agentic_erp.gateway.contract import GatewayFault, canonical_uuid
from synora_agentic_erp.gateway.security import record_gateway_audit
from synora_agentic_erp.governance.contracts import ExecutionReceipt, create_execution_receipt
from synora_agentic_erp.governance.execution import (
    _close_run,
    _insert_reservation,
    _lease_expired,
    _load_action_from_doc,
    _load_readable_target,
    _move_run_to_executing,
    _now_timestamp,
    _persist_receipt,
    _receipt_input,
    _reconciliation_evidence,
    _reservation_by_key,
    _reservation_dict,
    _reservation_identity_matches,
    _run_context,
    _safe_key,
    _serialize_receipt_for_actor,
    _update_reservation,
)
from synora_agentic_erp.governance.execution_contracts import (
    ReadBackMismatch,
    ReconciliationClassification,
    classify_reconciliation,
    execution_key,
    map_execution_error,
    purchase_order_values,
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
    serialize_action,
    transition_action_state,
    transition_execution_receipt,
)

TARGET_DOCTYPE = "Purchase Order"
ACTION_TYPE = "CREATE_PO_DRAFT"
WRITER_NAME = "governed.purchase_order.create"
WRITER_VERSION = "1"


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
    """Recheck the current read permission before returning cached success."""

    _reservation_identity_matches(reservation, action, execution_key(action), actor)
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
        verify_purchase_order_read_back,
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
    """Record a post-reservation PO failure without retrying the ERP writer."""

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
        # The STARTED reservation stays visible if recovery itself cannot commit.
        try:
            frappe.db.rollback()
        except Exception:
            pass


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
            "Purchase Order Item",
            filters={"item_code": ["in", item_codes], "warehouse": ["in", warehouses]},
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
                "supplier": payload["supplier"],
                "company": payload["company"],
                "transaction_date": payload["transaction_date"],
                "schedule_date": payload["schedule_date"],
                "currency": payload["currency"],
                "buying_price_list": payload["buying_price_list"],
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
            matches.append((name, verify_purchase_order_read_back(action, target)))
        except ReadBackMismatch, frappe.DoesNotExistError:
            continue
    return names, matches


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
            verify_purchase_order_read_back,
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
            {"doctype": TARGET_DOCTYPE, "name": str(receipt_doc.target_name), "docstatus": 0}
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


def reconcile_purchase_order(
    action_id: object,
    expected_digest: object,
    idempotency_key: object,
    correlation_id: object,
) -> dict[str, Any]:
    """Read ERP state and close one uncertain PO reservation without writing ERP."""

    safe_action_id = canonical_uuid(action_id, "action_id")
    safe_digest = _safe_digest(expected_digest)
    safe_key = _safe_key(idempotency_key)
    safe_correlation = canonical_uuid(correlation_id, "correlation_id")
    actor = _actor()
    run = _lock_run_for_action(safe_action_id)
    action_doc, action, locked = _lock_action(safe_action_id)
    if action.run_id != run.name or action.proposal_digest != safe_digest:
        raise GatewayFault("CONFLICT", "reconciliation digest or Run conflicts", 409)
    if action.idempotency_key != safe_key or action.action_type != ACTION_TYPE:
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
    evidence = _reconciliation_evidence(classification, candidate_names, matches, safe_correlation)
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
            "MANUAL_INTERVENTION", "Action state is no longer APPROVED"
        )
        evidence = _reconciliation_evidence(
            classification, candidate_names, matches, safe_correlation
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
    receipt = _reconciliation_receipt(
        action,
        run,
        reservation,
        receipt_doc,
        final_state=classification.result_status,
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
            reason="Purchase Order Draft found by read-only reconciliation",
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
        _close_run(
            run,
            "FAILED"
            if classification.result_status == "RECONCILED_FAILURE"
            else "RECONCILIATION_REQUIRED",
            safe_correlation,
        )
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


def execute_purchase_order(
    action_id: object,
    expected_digest: object,
    idempotency_key: object,
    correlation_id: object,
) -> dict[str, Any]:
    """Execute exactly one approved Purchase Order Draft."""

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
    if action.action_type != ACTION_TYPE:
        raise GatewayFault("INVALID_INPUT", "only CREATE_PO_DRAFT is supported", 400)
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
        if str(existing.status) in {"FAILED", "RECONCILED_FAILURE", "MANUAL_INTERVENTION"}:
            raise GatewayFault("CONFLICT", "execution is finalized and cannot be retried", 409)
        raise GatewayFault("CONFLICT", "execution is already reserved and cannot be retried", 409)

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
        if str(reservation.status) in {"FAILED", "RECONCILED_FAILURE", "MANUAL_INTERVENTION"}:
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
        values = purchase_order_values(action)
        target = frappe.get_doc(values)
        # ERPNext's controller populates stock UOM, conversion and base amount
        # fields; this is the same normal path used by its official test helper.
        target.set_missing_values()
        target.insert()
        target = frappe.get_doc(TARGET_DOCTYPE, target.name)
        verified = verify_purchase_order_read_back(action, target)
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
            reason="Purchase Order Draft created and read back",
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
        raise GatewayFault(category, "governed Purchase Order execution failed", status) from error


__all__ = ["execute_purchase_order", "reconcile_purchase_order"]
