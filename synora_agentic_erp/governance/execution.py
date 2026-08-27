"""Frappe-side executor for the first governed Material Request Draft.

The module is intentionally target-specific.  It accepts only identifiers for
an immutable approved action; the business payload is reconstructed from the
stored action and reaches ERPNext through the normal Document controller.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
    execution_key,
    map_execution_error,
    material_request_values,
    verify_material_request_read_back,
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
)

RESERVATION_TRANSITION_FLAG = "synora_execution_reservation_transition"
TARGET_DOCTYPE = "Material Request"
WRITER_NAME = "governed.material_request.create"
WRITER_VERSION = "1"


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
        "status": str(doc.status),
        "target_name": str(doc.target_name or "") or None,
        "receipt_id": str(doc.receipt or "") or None,
        "response_category": str(doc.response_category or "") or None,
        "failure_category": str(doc.failure_category or "") or None,
        "started_at": str(doc.started_at),
        "completed_at": str(doc.completed_at or "") or None,
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
        or str(doc.target_doctype) != TARGET_DOCTYPE
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
            "target_doctype": TARGET_DOCTYPE,
            "executor": actor,
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
    if status not in {"SUCCEEDED", "FAILED", "RECONCILIATION_REQUIRED"}:
        raise GatewayFault("INVALID_INPUT", "execution reservation status is invalid")
    if str(doc.status) != "STARTED":
        if str(doc.status) == status:
            return doc
        raise GatewayFault("CONFLICT", "execution reservation is already finalized", 409)
    doc.status = status
    if target_name is not None:
        doc.target_name = target_name
    if receipt_id is not None:
        doc.receipt = receipt_id
    if response_category is not None:
        doc.response_category = response_category
    if failure_category is not None:
        doc.failure_category = failure_category
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
            "target_doctype": TARGET_DOCTYPE if target_name else None,
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
    return persist_execution_receipt(receipt, verified_execution=receipt.final_state == "SUCCEEDED")


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
    if str(action_doc.state) != "EXECUTED" or str(reservation.status) != "SUCCEEDED":
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
        target = frappe.get_doc(TARGET_DOCTYPE, target_name)
        verified = verify_material_request_read_back(action, target)
        receipt_doc = frappe.get_doc("Synora Execution Receipt", receipt_name)
    except frappe.DoesNotExistError as error:
        raise GatewayFault(
            "UNCERTAIN_RESULT", "verified ERP outcome is unavailable", 503
        ) from error
    if receipt_doc.final_state != "SUCCEEDED" or receipt_doc.target_name != target_name:
        raise GatewayFault("UNCERTAIN_RESULT", "verified Receipt conflicts with target", 503)
    if json.loads(receipt_doc.verified_fields_json) != verified:
        raise GatewayFault("UNCERTAIN_RESULT", "ERP read-back no longer matches Receipt", 503)
    _audit(run, str(reservation.correlation_id), "CACHED")
    frappe.db.commit()
    return _success_response(
        action_doc,
        run,
        reservation,
        serialize_receipt(receipt_doc),
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
        if str(existing.status) == "SUCCEEDED":
            return _replay_success(action_doc, action, run, existing, actor)
        if str(existing.status) == "RECONCILIATION_REQUIRED":
            raise GatewayFault("UNCERTAIN_RESULT", "execution result requires reconciliation", 503)
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
        if str(reservation.status) == "SUCCEEDED":
            frappe.db.rollback()
            run = _lock_run_for_action(safe_action_id)
            action_doc, action, _ = _lock_action(safe_action_id)
            return _replay_success(action_doc, action, run, reservation, actor)
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


__all__ = ["execute_material_request"]
