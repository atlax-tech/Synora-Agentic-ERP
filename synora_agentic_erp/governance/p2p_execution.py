"""Governed P2P document actions for Phase 10.

Every function in this module receives only a stored, approved action.  ERPNext
controllers remain the sole business writer; this module supplies typed input,
separation-of-duties checks, a durable reservation, and a read-back Receipt.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import frappe

from synora_agentic_erp.agent.service import _set_run_state
from synora_agentic_erp.gateway.contract import GatewayFault, canonical_uuid
from synora_agentic_erp.gateway.security import record_gateway_audit
from synora_agentic_erp.governance.contracts import (
    ENABLED_P2P_ACTION_TYPES,
    P2P_ACTION_TYPES,
    TARGET_DOCTYPES,
    create_execution_receipt,
)
from synora_agentic_erp.governance.execution import (
    _insert_reservation,
    _lease_expired,
    _load_action_from_doc,
    _move_run_to_executing,
    _now_timestamp,
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
    execution_key,
    map_execution_error,
    p2p_read_back,
    p2p_values,
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
    persist_execution_receipt,
    serialize_action,
    serialize_receipt,
    transition_action_state,
    transition_execution_receipt,
)

P2P_WRITER_NAME = "governed.p2p"
P2P_WRITER_VERSION = "1"
SOURCE_ACTIONS = {
    "SUBMIT_PO",
    "CANCEL_PO",
    "SUBMIT_PR",
    "CANCEL_PR",
    "SUBMIT_PI",
    "CANCEL_PI",
    "SUBMIT_PAYMENT_ENTRY",
    "CANCEL_PAYMENT_ENTRY",
}
CREATE_ACTIONS = {
    "CREATE_PR_DRAFT",
    "CREATE_PI_DRAFT",
    "CREATE_PAYMENT_ENTRY_DRAFT",
}
SOURCE_CHILD_DOCTYPES = {
    "CREATE_PR_DRAFT": "Purchase Order Item",
    "CREATE_PI_DRAFT": "Purchase Receipt Item",
}
KNOWN_CONTROLLER_FAILURES = frozenset(
    {
        "ValidationError",
        "MandatoryError",
        "LinkValidationError",
        "InvalidStatusError",
        "UniqueValidationError",
        "PermissionError",
        "NotPermittedError",
        "DoesNotExistError",
    }
)


def _audit(run: Any, correlation_id: str, outcome: str, error_code: str | None = None) -> None:
    record_gateway_audit(
        _run_context(run), P2P_WRITER_NAME, P2P_WRITER_VERSION, correlation_id, outcome, error_code
    )


def _load_target(action: Any, *, expected_docstatus: int | None = None) -> Any:
    """Load a source document only after a session-scoped parent query."""

    payload = action.payload
    doctype = TARGET_DOCTYPES[action.action_type]
    if not frappe.has_permission(doctype, "read", user=frappe.session.user):
        raise GatewayFault("PERMISSION_DENIED", "current ERP permission is insufficient", 403)
    filters: dict[str, Any] = {
        "name": payload["source_name"],
        "company": payload["company"],
    }
    if expected_docstatus is not None:
        filters["docstatus"] = expected_docstatus
    rows = frappe.get_list(
        doctype,
        filters=filters,
        fields=["name"],
        user=frappe.session.user,
        limit=1,
    )
    if not rows:
        raise GatewayFault("PERMISSION_DENIED", "source document is outside current ERP scope", 403)
    try:
        return frappe.get_doc(doctype, payload["source_name"])
    except frappe.DoesNotExistError as error:
        raise GatewayFault("ERP_NOT_FOUND", "source document is unavailable", 404) from error


def _load_replay_target(action: Any, target_name: str, actor: str) -> Any:
    """Read a created target only after current actor and company scope checks."""

    doctype = TARGET_DOCTYPES[action.action_type]
    if not frappe.has_permission(doctype, "read", user=actor):
        raise GatewayFault("PERMISSION_DENIED", "current ERP permission is insufficient", 403)
    rows = frappe.get_list(
        doctype,
        filters={"name": target_name, "company": action.payload["company"]},
        fields=["name"],
        user=actor,
        limit=1,
    )
    if not rows:
        raise GatewayFault("PERMISSION_DENIED", "target is outside current ERP scope", 403)
    try:
        return frappe.get_doc(doctype, target_name)
    except frappe.DoesNotExistError as error:
        raise GatewayFault("ERP_NOT_FOUND", "target document is unavailable", 404) from error


def _build_pr(action: Any) -> Any:
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

    source_rows = [item["source_row"] for item in action.payload["items"]]
    target = make_purchase_receipt(
        action.payload["source_name"],
        args={"filtered_children": source_rows},
    )
    requested = {item["source_row"]: item for item in action.payload["items"]}
    for row in target.items:
        source_row = str(getattr(row, "purchase_order_item", "") or "")
        item = requested.get(source_row)
        if item is None:
            raise GatewayFault("CONFLICT", "mapped receipt contains an unapproved source row", 409)
        row.qty = item["qty"]
    target.posting_date = action.payload["transaction_date"]
    return target


def _build_pi(action: Any) -> Any:
    from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice

    source_rows = [item["source_row"] for item in action.payload["items"]]
    target = make_purchase_invoice(
        action.payload["source_name"],
        args={"filtered_children": source_rows},
    )
    requested = {item["source_row"]: item for item in action.payload["items"]}
    for row in target.items:
        source_row = str(getattr(row, "pr_detail", "") or "")
        item = requested.get(source_row)
        if item is None:
            raise GatewayFault("CONFLICT", "mapped invoice contains an unapproved source row", 409)
        row.qty = item["qty"]
        row.rate = item["rate"]
    target.posting_date = action.payload["transaction_date"]
    return target


def _build_target(action: Any) -> Any:
    if action.action_type == "CREATE_PR_DRAFT":
        return _build_pr(action)
    if action.action_type == "CREATE_PI_DRAFT":
        return _build_pi(action)
    if action.action_type == "CREATE_PAYMENT_ENTRY_DRAFT":
        target = frappe.get_doc(p2p_values(action))
        target.set_missing_values()
        return target
    raise GatewayFault("INVALID_INPUT", "action does not create a P2P draft", 400)


def _load_source_target(action: Any) -> Any:
    is_submit = action.action_type.startswith("SUBMIT_")
    return _load_target(action, expected_docstatus=0 if is_submit else 1)


def _lock_p2p_source(action: Any) -> None:
    """Serialize competing draft conversions on the reviewed source row."""

    child_doctype = SOURCE_CHILD_DOCTYPES.get(str(action.action_type))
    if child_doctype is None:
        return
    payload = action.payload
    table = f"tab{child_doctype}"
    for item in sorted(payload["items"], key=lambda value: value["source_row"]):
        rows = frappe.db.sql(
            f"""
            SELECT name
            FROM `{table}`
            WHERE name = %s AND parent = %s
            FOR UPDATE
            """,
            (item["source_row"], payload["source_name"]),
            as_dict=True,
        )
        if not rows:
            raise GatewayFault("CONFLICT", "reviewed source row is no longer available", 409)


def _apply_source_action(action: Any, target: Any) -> Any:
    is_submit = action.action_type.startswith("SUBMIT_")
    if is_submit:
        return target.submit()
    return target.cancel()


def _persist_receipt(
    action: Any,
    run: Any,
    reservation: Any,
    *,
    final_state: str,
    response_category: str,
    failure_category: str | None,
    target: Any | None,
    verified_fields: dict[str, Any],
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    approval = _latest_approval(action.action_id, action.proposal_digest, "ALLOW")
    receipt = create_execution_receipt(
        {
            "receipt_id": str(uuid4()),
            "action_id": action.action_id,
            "run_id": run.name,
            "idempotency_key": action.idempotency_key,
            "initiator": action.initiator,
            "approver": str(approval.actor) if approval is not None else None,
            "executor": str(reservation.executor),
            "proposal_digest": action.proposal_digest,
            "target_doctype": TARGET_DOCTYPES[action.action_type] if target is not None else None,
            "target_name": str(target.name) if target is not None else None,
            "verified_fields": verified_fields,
            "response_category": response_category,
            "failure_category": failure_category,
            "final_state": final_state,
            "started_at": str(reservation.started_at),
            "completed_at": _now_timestamp(),
            "correlation_id": str(reservation.correlation_id),
            "reconciliation_evidence": evidence,
        }
    )
    return persist_execution_receipt(
        receipt,
        verified_execution=final_state in {"SUCCEEDED", "RECONCILED_SUCCESS"},
    )


def _success_response(
    action_doc: Any,
    run: Any,
    reservation: Any,
    receipt: dict[str, Any],
    target: Any,
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
        "action": serialize_action(action_doc, allowed_actor=str(reservation.executor)),
        "reservation": _reservation_dict(reservation),
        "target": {
            "doctype": TARGET_DOCTYPES[action_doc.action_type],
            "name": str(target.name),
            "docstatus": int(getattr(target, "docstatus", 0) or 0),
        },
        "receipt": receipt,
    }


def _replay(action_doc: Any, action: Any, run: Any, reservation: Any, actor: str) -> dict[str, Any]:
    _reservation_identity_matches(reservation, action, execution_key(action), actor)
    receipt_name = str(reservation.receipt or "")
    target_name = str(reservation.target_name or "")
    if str(action_doc.state) != "EXECUTED" or not receipt_name or not target_name:
        raise GatewayFault("UNCERTAIN_RESULT", "verified P2P replay evidence is incomplete", 503)
    receipt_doc = frappe.get_doc("Synora Execution Receipt", receipt_name)
    target = (
        _load_target(action)
        if action.action_type in SOURCE_ACTIONS
        else _load_replay_target(action, target_name, actor)
    )
    verified = p2p_read_back(action, target)
    recorded = json.loads(receipt_doc.verified_fields_json)
    if recorded != verified:
        raise GatewayFault("UNCERTAIN_RESULT", "ERP read-back no longer matches Receipt", 503)
    _audit(run, str(reservation.correlation_id), "CACHED")
    return _success_response(
        action_doc,
        run,
        reservation,
        serialize_receipt(receipt_doc, allowed_actor=actor),
        target,
    )


def _finalize_failure(
    action_id: str,
    reservation_id: str,
    *,
    category: str,
    failure: str,
    uncertain: bool,
    target: Any | None = None,
) -> None:
    try:
        reservation = frappe.get_doc("Synora Execution Reservation", reservation_id)
        action_doc = frappe.get_doc("Synora Proposed Action", action_id)
        run = frappe.get_doc("Synora Agent Run", reservation.run)
        action = _load_action_from_doc(action_doc)
        if str(reservation.status) != "STARTED":
            return
        persisted_target = target
        if target is not None:
            target_name = str(getattr(target, "name", "") or "")
            if not target_name or not frappe.db.exists(
                TARGET_DOCTYPES[action.action_type], target_name
            ):
                persisted_target = None
        final_state = "RECONCILIATION_REQUIRED" if uncertain else "FAILED"
        stored = _persist_receipt(
            action,
            run,
            reservation,
            final_state=final_state,
            response_category="UNCERTAIN_RESULT" if uncertain else category,
            failure_category=failure,
            target=persisted_target,
            verified_fields={},
            evidence={"reason": failure} if uncertain else None,
        )
        _update_reservation(
            reservation,
            final_state,
            target_name=str(persisted_target.name) if persisted_target is not None else None,
            receipt_id=stored["receipt_id"],
            response_category="UNCERTAIN_RESULT" if uncertain else category,
            failure_category=failure,
        )
        if str(action_doc.state) == "APPROVED":
            transition_action_state(
                action.action_id,
                "EXPIRED",
                expected_version=int(action_doc.state_version),
                reason=failure[:2_000],
                correlation_id=str(reservation.correlation_id),
                approval_digest=action.proposal_digest,
            )
        _audit(run, str(reservation.correlation_id), "REJECTED", failure)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()


def execute_p2p_action(
    action_id: object,
    expected_digest: object,
    idempotency_key: object,
    correlation_id: object,
) -> dict[str, Any]:
    """Execute one approved P2P action through the native ERP controller."""

    safe_action_id = canonical_uuid(action_id, "action_id")
    safe_digest = _safe_digest(expected_digest)
    safe_key = _safe_key(idempotency_key)
    safe_correlation = canonical_uuid(correlation_id, "correlation_id")
    actor = _actor()
    run = _lock_run_for_action(safe_action_id)
    action_doc, action, locked = _lock_action(safe_action_id)
    if action.action_type not in P2P_ACTION_TYPES:
        raise GatewayFault("INVALID_INPUT", "only Phase 10 P2P actions are supported", 400)
    if action.action_type not in ENABLED_P2P_ACTION_TYPES:
        raise GatewayFault(
            "CONFLICT",
            "P2P action is not enabled in the current Phase 10 increment",
            409,
        )
    if action.run_id != run.name or action.proposal_digest != safe_digest:
        raise GatewayFault("CONFLICT", "execution digest or Run conflicts", 409)
    if action.idempotency_key != safe_key:
        raise GatewayFault("CONFLICT", "idempotency key conflicts", 409)
    key = execution_key(action)
    existing = _reservation_by_key(safe_key, lock=True)
    if existing is not None:
        _reservation_identity_matches(existing, action, key, actor)
        if str(existing.status) in {"SUCCEEDED", "RECONCILED_SUCCESS"}:
            return _replay(action_doc, action, run, existing, actor)
        if str(existing.status) == "STARTED" and _lease_expired(existing):
            return reconcile_p2p_action(
                safe_action_id,
                safe_digest,
                safe_key,
                safe_correlation,
            )
        if str(existing.status) == "RECONCILIATION_REQUIRED":
            raise GatewayFault("UNCERTAIN_RESULT", "execution result requires reconciliation", 503)
        raise GatewayFault("CONFLICT", "execution is already reserved or finalized", 409)
    pre_execute_recheck(safe_action_id, safe_digest, safe_key)
    run = _lock_run_for_action(safe_action_id)
    action_doc, action, locked = _lock_action(safe_action_id)
    reservation, created = _insert_reservation(action, key, actor)
    if not created:
        raise GatewayFault("CONFLICT", "execution is already reserved", 409)
    try:
        _move_run_to_executing(run)
        frappe.db.commit()
    except Exception as error:
        frappe.db.rollback()
        raise GatewayFault(
            "CONFLICT", "execution reservation could not be committed", 409
        ) from error

    operation_started = False
    target: Any | None = None
    try:
        pre_execute_recheck(safe_action_id, safe_digest, safe_key)
        run = _lock_run_for_action(safe_action_id)
        action_doc, action, locked = _lock_action(safe_action_id)
        _lock_p2p_source(action)
        if action.action_type in CREATE_ACTIONS:
            # The source row lock serializes two stale approvals.  Re-run the
            # quantity/open-draft checks after the winner commits its target.
            pre_execute_recheck(safe_action_id, safe_digest, safe_key)
            run = _lock_run_for_action(safe_action_id)
            action_doc, action, locked = _lock_action(safe_action_id)
        if action.action_type in SOURCE_ACTIONS:
            target = _load_source_target(action)
            operation_started = True
            target = _apply_source_action(action, target)
        else:
            target = _build_target(action)
            operation_started = True
            target.insert()
        target = frappe.get_doc(TARGET_DOCTYPES[action.action_type], target.name)
        verified = p2p_read_back(action, target)
        stored_receipt = _persist_receipt(
            action,
            run,
            reservation,
            final_state="SUCCEEDED",
            response_category="ERP_SUCCESS",
            failure_category=None,
            target=target,
            verified_fields=verified,
        )
        transition_action_state(
            action.action_id,
            "EXECUTED",
            expected_version=int(locked["state_version"]),
            reason=f"{TARGET_DOCTYPES[action.action_type]} action succeeded and was read back",
            correlation_id=safe_correlation,
            approval_digest=action.proposal_digest,
        )
        _update_reservation(
            reservation,
            "SUCCEEDED",
            target_name=str(target.name),
            receipt_id=stored_receipt["receipt_id"],
            response_category="ERP_SUCCESS",
        )
        _audit(run, safe_correlation, "SUCCEEDED")
        frappe.db.commit()
        stored_action_doc = frappe.get_doc("Synora Proposed Action", action.action_id)
        return _success_response(stored_action_doc, run, reservation, stored_receipt, target)
    except Exception as error:
        category, failure, status = map_execution_error(error)
        known_controller_failure = failure in KNOWN_CONTROLLER_FAILURES
        uncertain = category == "UNCERTAIN_RESULT" or (
            operation_started and not known_controller_failure
        )
        frappe.db.rollback()
        _finalize_failure(
            safe_action_id,
            str(reservation.reservation_id),
            category=category,
            failure=failure,
            uncertain=uncertain,
            target=target,
        )
        raise GatewayFault(
            "UNCERTAIN_RESULT" if uncertain else category,
            "governed P2P execution failed",
            503 if uncertain else status,
        ) from error


def reconcile_p2p_action(
    action_id: object,
    expected_digest: object,
    idempotency_key: object,
    correlation_id: object,
) -> dict[str, Any]:
    """Classify an uncertain P2P result; this endpoint never retries a writer."""

    safe_action_id = canonical_uuid(action_id, "action_id")
    safe_digest = _safe_digest(expected_digest)
    safe_key = _safe_key(idempotency_key)
    safe_correlation = canonical_uuid(correlation_id, "correlation_id")
    actor = _actor()
    run = _lock_run_for_action(safe_action_id)
    action_doc, action, _ = _lock_action(safe_action_id)
    if action.action_type not in P2P_ACTION_TYPES or action.proposal_digest != safe_digest:
        raise GatewayFault("CONFLICT", "reconciliation identity conflicts", 409)
    if action.action_type not in ENABLED_P2P_ACTION_TYPES:
        raise GatewayFault(
            "CONFLICT",
            "P2P action is not enabled in the current Phase 10 increment",
            409,
        )
    reservation = _reservation_by_key(safe_key, lock=True)
    if reservation is None:
        raise GatewayFault("NOT_FOUND", "P2P execution reservation is unavailable", 404)
    _reservation_identity_matches(reservation, action, execution_key(action), actor)
    if str(reservation.status) not in {
        "STARTED",
        "RECONCILIATION_REQUIRED",
        "SUCCEEDED",
        "RECONCILED_SUCCESS",
        "FAILED",
        "RECONCILED_FAILURE",
        "MANUAL_INTERVENTION",
    }:
        raise GatewayFault("CONFLICT", "P2P execution reservation status is invalid", 409)
    receipt_doc = None
    if reservation.receipt:
        try:
            receipt_doc = frappe.get_doc("Synora Execution Receipt", reservation.receipt)
        except frappe.DoesNotExistError as error:
            raise GatewayFault(
                "UNCERTAIN_RESULT", "P2P reconciliation Receipt is unavailable", 503
            ) from error

    def response(result_status: str, evidence: dict[str, Any]) -> dict[str, Any]:
        receipt = (
            _serialize_receipt_for_actor(action, reservation, receipt_doc, actor)
            if receipt_doc is not None
            else None
        )
        target = None
        if receipt_doc is not None and receipt_doc.target_name:
            target = {
                "doctype": str(receipt_doc.target_doctype),
                "name": str(receipt_doc.target_name),
            }
        return {
            "ok": result_status in {"RECONCILED_SUCCESS", "RECONCILED_FAILURE"},
            "schema_version": "1",
            "result_status": result_status,
            "can_retry": False,
            "correlation_id": safe_correlation,
            "run": {
                "run_id": str(run.name),
                "run_state": str(run.run_state),
                "state_version": int(run.state_version),
            },
            "action": serialize_action(action_doc, allowed_actor=actor),
            "reservation": _reservation_dict(reservation),
            "receipt": receipt,
            "target": target,
            "reconciliation": evidence,
        }

    current_status = str(reservation.status)
    if current_status in {
        "SUCCEEDED",
        "RECONCILED_SUCCESS",
        "FAILED",
        "RECONCILED_FAILURE",
        "MANUAL_INTERVENTION",
    }:
        return response(
            str(receipt_doc.final_state) if receipt_doc is not None else current_status,
            {"reason": "execution is already finalized", "can_retry": False},
        )
    if not _lease_expired(reservation):
        return response(
            "RECONCILIATION_REQUIRED",
            {
                "reason": "execution lease is still active",
                "next_reconcile_at": str(reservation.lease_expires_at),
                "can_retry": False,
            },
        )

    # A source action has a stable target identity in its reviewed payload.
    # Created-document actions may use the reservation target; without either
    # reference, reconciliation must stop for a human instead of guessing.
    target_name = str(reservation.target_name or "")
    if not target_name and action.action_type in SOURCE_ACTIONS:
        target_name = str(action.payload["source_name"])
    target = None
    verified: dict[str, Any] = {}
    if target_name:
        try:
            target = (
                _load_target(action)
                if action.action_type in SOURCE_ACTIONS
                else _load_replay_target(action, target_name, actor)
            )
            verified = p2p_read_back(action, target)
        except ReadBackMismatch:
            target = None
            verified = {}
        except GatewayFault as error:
            if error.code == "PERMISSION_DENIED":
                raise
            target = None
            verified = {}

    reconciled = bool(target is not None and verified and str(action_doc.state) == "APPROVED")
    final_state = "RECONCILED_SUCCESS" if reconciled else "MANUAL_INTERVENTION"
    response_category = "ERP_SUCCESS" if reconciled else "UNCERTAIN_RESULT"
    failure_category = (
        None
        if reconciled
        else str(reservation.failure_category or "AMBIGUOUS_P2P_RESULT")
    )
    evidence = {
        "reason": "one reviewed P2P target read back"
        if reconciled
        else "target identity or critical fields could not be verified",
        "target_name": target_name or None,
        "verified": reconciled,
        "reconciliation_correlation_id": safe_correlation,
    }
    if current_status == "STARTED":
        _update_reservation(
            reservation,
            "RECONCILIATION_REQUIRED",
            response_category="UNCERTAIN_RESULT",
            failure_category=str(reservation.failure_category or "RECONCILIATION_PENDING"),
        )
        reservation = frappe.get_doc("Synora Execution Reservation", reservation.name)

    approval = _latest_approval(action.action_id, action.proposal_digest, "ALLOW")
    receipt = create_execution_receipt(
        {
            "receipt_id": str(receipt_doc.receipt_id) if receipt_doc is not None else str(uuid4()),
            "action_id": action.action_id,
            "run_id": run.name,
            "idempotency_key": action.idempotency_key,
            "initiator": action.initiator,
            "approver": (
                str(receipt_doc.approver)
                if receipt_doc is not None and receipt_doc.approver
                else str(approval.actor)
                if approval is not None
                else None
            ),
            "executor": str(reservation.executor),
            "proposal_digest": action.proposal_digest,
            "target_doctype": TARGET_DOCTYPES[action.action_type] if reconciled else None,
            "target_name": target_name if reconciled else None,
            "verified_fields": verified,
            "response_category": response_category,
            "failure_category": failure_category,
            "final_state": final_state,
            "started_at": str(receipt_doc.started_at)
            if receipt_doc is not None
            else str(reservation.started_at),
            "completed_at": _now_timestamp(),
            "correlation_id": str(receipt_doc.correlation_id)
            if receipt_doc is not None
            else str(reservation.correlation_id),
            "reconciliation_evidence": evidence,
        }
    )
    if receipt_doc is None:
        stored = persist_execution_receipt(
            receipt,
            verified_execution=final_state == "RECONCILED_SUCCESS",
        )
        receipt_doc = frappe.get_doc("Synora Execution Receipt", stored["receipt_id"])
    else:
        stored = transition_execution_receipt(receipt)
        receipt_doc = frappe.get_doc("Synora Execution Receipt", stored["receipt_id"])

    if reconciled:
        transition_action_state(
            action.action_id,
            "EXECUTED",
            expected_version=int(action_doc.state_version),
            reason="P2P target was found by read-only reconciliation",
            correlation_id=safe_correlation,
            approval_digest=action.proposal_digest,
        )
        _update_reservation(
            reservation,
            "RECONCILED_SUCCESS",
            target_name=target_name,
            receipt_id=str(stored["receipt_id"]),
            response_category="ERP_SUCCESS",
            failure_category=None,
        )
    else:
        if str(action_doc.state) == "APPROVED":
            transition_action_state(
                action.action_id,
                "EXPIRED",
                expected_version=int(action_doc.state_version),
                reason=evidence["reason"][:2_000],
                correlation_id=safe_correlation,
                approval_digest=action.proposal_digest,
            )
        _update_reservation(
            reservation,
            "MANUAL_INTERVENTION",
            receipt_id=str(stored["receipt_id"]),
            response_category="UNCERTAIN_RESULT",
            failure_category=failure_category,
        )
        if str(run.run_state) == "EXECUTING":
            _set_run_state(run, "RECONCILIATION_REQUIRED")
    _audit(run, safe_correlation, "CACHED" if reconciled else "REJECTED", final_state)
    frappe.db.commit()
    action_doc = frappe.get_doc("Synora Proposed Action", action.action_id)
    return response(final_state, evidence)


__all__ = ["execute_p2p_action", "reconcile_p2p_action"]
