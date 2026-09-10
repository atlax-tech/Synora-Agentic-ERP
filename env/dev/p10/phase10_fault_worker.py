"""Isolated worker for the Phase 10 P2P fault matrix.

The functions in this module are loaded only by a test-process ``bench
console``.  They are deliberately not whitelisted and accept no HTTP fault
control.  Every invocation uses a fresh process so the parent can read the
durable reservation, Receipt, and ERP target through a new connection.
"""

from __future__ import annotations

import json
import os
from typing import Any

import frappe


def _execute(
    action_id: str,
    expected_digest: str,
    idempotency_key: str,
    correlation_id: str,
    actor: str,
) -> Any:
    from synora_agentic_erp.governance.p2p_execution import execute_p2p_action

    frappe.set_user(actor)
    return execute_p2p_action(action_id, expected_digest, idempotency_key, correlation_id)


def _execute_expect_error(
    action_id: str,
    expected_digest: str,
    idempotency_key: str,
    correlation_id: str,
    actor: str,
) -> None:
    try:
        _execute(action_id, expected_digest, idempotency_key, correlation_id, actor)
    except Exception as error:
        print(
            "P10_EXPECTED_EXCEPTION "
            + json.dumps(
                {"type": type(error).__name__, "message": str(error)[:240]},
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )


def _reservation_started(action_id: str) -> bool:
    return bool(
        frappe.db.exists(
            "Synora Execution Reservation",
            {"action": action_id, "status": "STARTED"},
        )
    )


def crash_after_reservation(
    action_id: str,
    expected_digest: str,
    idempotency_key: str,
    correlation_id: str,
    actor: str,
) -> None:
    """Exit after the durable STARTED reservation commit."""

    import synora_agentic_erp.governance.execution as governance_execution

    governance_execution.LEASE_SECONDS = 0
    original_commit = frappe.db.commit

    def commit_then_exit(*args: Any, **kwargs: Any) -> Any:
        result = original_commit(*args, **kwargs)
        if _reservation_started(action_id):
            print("P10_RESERVATION_COMMITTED_EXIT", flush=True)
            os._exit(137)
        return result

    frappe.db.commit = commit_then_exit
    _execute(action_id, expected_digest, idempotency_key, correlation_id, actor)


def fail_before_erp_call(
    action_id: str,
    expected_digest: str,
    idempotency_key: str,
    correlation_id: str,
    actor: str,
) -> None:
    """Raise before the native ERP controller or target insert is called."""

    from synora_agentic_erp.governance import p2p_execution

    def fail_source(*args: Any, **kwargs: Any) -> Any:
        raise frappe.ValidationError("phase10 fault matrix: before ERP call")

    def fail_target(*args: Any, **kwargs: Any) -> Any:
        raise frappe.ValidationError("phase10 fault matrix: before ERP call")

    p2p_execution._apply_source_action = fail_source
    p2p_execution._build_target = fail_target
    _execute_expect_error(action_id, expected_digest, idempotency_key, correlation_id, actor)


def crash_after_erp_call(
    action_id: str,
    expected_digest: str,
    idempotency_key: str,
    correlation_id: str,
    actor: str,
) -> None:
    """Exercise the post-ERP boundary in a fresh process.

    Source actions use the native controller result and its possible commit
    boundary.  Created-document actions deliberately exit after the native
    insert but before the surrounding transaction commits; process teardown
    therefore proves the atomic rollback path rather than inventing a split
    production transaction.
    """

    import synora_agentic_erp.governance.execution as governance_execution
    from synora_agentic_erp.governance import p2p_execution

    governance_execution.LEASE_SECONDS = 0
    original_apply = p2p_execution._apply_source_action
    original_build = p2p_execution._build_target

    def crash_source(action: Any, target: Any) -> Any:
        original_apply(action, target)
        frappe.db.commit()
        print("P10_NATIVE_ERP_COMMITTED_EXIT", flush=True)
        os._exit(137)

    def crash_target(action: Any) -> Any:
        target = original_build(action)
        target.insert()
        print("P10_ATOMIC_ERP_INSERT_EXIT", flush=True)
        os._exit(137)

    p2p_execution._apply_source_action = crash_source
    p2p_execution._build_target = crash_target
    _execute(action_id, expected_digest, idempotency_key, correlation_id, actor)


def fail_before_receipt(
    action_id: str,
    expected_digest: str,
    idempotency_key: str,
    correlation_id: str,
    actor: str,
) -> None:
    """Raise immediately before the success Receipt is persisted."""

    import synora_agentic_erp.governance.execution as governance_execution
    from synora_agentic_erp.governance import p2p_execution

    governance_execution.LEASE_SECONDS = 0

    def fail_receipt(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("phase10 fault matrix: before Receipt persistence")

    p2p_execution._persist_receipt = fail_receipt
    _execute_expect_error(action_id, expected_digest, idempotency_key, correlation_id, actor)


def reconcile(
    action_id: str,
    expected_digest: str,
    idempotency_key: str,
    correlation_id: str,
    actor: str,
) -> None:
    """Perform only the read-only reconciliation operation after a crash."""

    from synora_agentic_erp.governance.p2p_execution import reconcile_p2p_action

    frappe.set_user(actor)
    result = reconcile_p2p_action(
        action_id,
        expected_digest,
        idempotency_key,
        correlation_id,
    )
    print("P10_RECONCILE " + json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)


def snapshot(action_id: str) -> None:
    """Print a redacted, independent readback for one action."""

    frappe.set_user("Administrator")
    action = frappe.get_doc("Synora Proposed Action", action_id)
    run = frappe.get_doc("Synora Agent Run", action.run)
    reservation_rows = frappe.get_all(
        "Synora Execution Reservation",
        filters={"action": action_id},
        fields=[
            "reservation_id",
            "status",
            "attempt",
            "target_name",
            "receipt",
            "response_category",
            "failure_category",
            "lease_expires_at",
        ],
        order_by="creation desc",
        limit_page_length=1,
        ignore_permissions=True,
    )
    reservation = dict(reservation_rows[0]) if reservation_rows else None
    receipt = None
    if reservation and reservation.get("receipt"):
        receipt_doc = frappe.get_doc("Synora Execution Receipt", reservation["receipt"])
        receipt = {
            "receipt_id": str(receipt_doc.receipt_id),
            "final_state": str(receipt_doc.final_state),
            "response_category": str(receipt_doc.response_category),
            "failure_category": str(receipt_doc.failure_category or "") or None,
            "target_doctype": str(receipt_doc.target_doctype or "") or None,
            "target_name": str(receipt_doc.target_name or "") or None,
        }
    target = None
    if reservation and reservation.get("target_name"):
        doctype = {
            "SUBMIT_PO": "Purchase Order",
            "CANCEL_PO": "Purchase Order",
            "CREATE_PR_DRAFT": "Purchase Receipt",
            "SUBMIT_PR": "Purchase Receipt",
            "CANCEL_PR": "Purchase Receipt",
            "CREATE_PI_DRAFT": "Purchase Invoice",
            "SUBMIT_PI": "Purchase Invoice",
            "CANCEL_PI": "Purchase Invoice",
            "CREATE_PAYMENT_ENTRY_DRAFT": "Payment Entry",
            "SUBMIT_PAYMENT_ENTRY": "Payment Entry",
            "CANCEL_PAYMENT_ENTRY": "Payment Entry",
        }.get(str(action.action_type))
        if doctype and frappe.db.exists(doctype, reservation["target_name"]):
            target_doc = frappe.get_doc(doctype, reservation["target_name"])
            target = {
                "doctype": doctype,
                "name": str(target_doc.name),
                "docstatus": int(target_doc.docstatus),
            }
    result = {
        "action_id": action_id,
        "action_type": str(action.action_type),
        "action_state": str(action.state),
        "action_state_version": int(action.state_version),
        "run_state": str(run.run_state),
        "reservation": reservation,
        "receipt": receipt,
        "target": target,
    }
    print(
        "P10_SNAPSHOT " + json.dumps(result, ensure_ascii=False, sort_keys=True, default=str),
        flush=True,
    )
