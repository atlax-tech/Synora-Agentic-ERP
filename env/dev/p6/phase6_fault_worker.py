"""One-shot bench-console helpers for Phase 6 real fault evidence.

These helpers are copied to ``/tmp`` by the real-fault harness and executed in
an isolated ``bench console`` process.  They are not installed as whitelisted
methods and never accept an HTTP fault parameter.
"""

from __future__ import annotations

import json
import os
from typing import Any

import frappe


def create_fixture() -> None:
    """Create one uniquely named Item and authoritative Item Price for a test run."""

    from uuid import uuid4

    item_code = f"SYNORA-P6-FAULT-{uuid4().hex[:12]}"
    frappe.get_doc(
        {
            "doctype": "Item",
            "item_code": item_code,
            "item_name": item_code,
            "item_group": "SYNORA-P1 Items",
            "stock_uom": "Unit",
            "is_stock_item": 1,
        }
    ).insert(ignore_permissions=True)
    frappe.get_doc(
        {
            "doctype": "Item Price",
            "item_code": item_code,
            "price_list": "SYNORA-P1 Buying CNY",
            "price_list_rate": 100,
            "currency": "CNY",
            "uom": "Unit",
            "supplier": "SYNORA-P1-Supplier-1",
            "buying": 1,
            "selling": 0,
            "valid_from": "2026-01-01",
        }
    ).insert(ignore_permissions=True)
    frappe.db.commit()
    print(f"P6_FIXTURE {item_code}", flush=True)


def crash_after_t1_commit(
    action_id: str,
    expected_digest: str,
    idempotency_key: str,
    correlation_id: str,
) -> None:
    """Exit the worker immediately after its durable STARTED reservation commit."""

    import synora_agentic_erp.governance.execution as governance_execution
    import synora_agentic_erp.governance.purchase_order_execution as execution

    # The zero-length lease makes the post-crash read-only reconciliation
    # immediately eligible.  This constant is changed only in this isolated
    # process; no production setting or HTTP argument controls it.
    governance_execution.LEASE_SECONDS = 0
    original_commit = frappe.db.commit

    def commit_then_crash(*args: Any, **kwargs: Any) -> Any:
        result = original_commit(*args, **kwargs)
        if frappe.db.exists(
            "Synora Execution Reservation",
            {"action": action_id, "status": "STARTED"},
        ):
            print(f"P6_T1_COMMITTED_WORKER_EXIT pid={os.getpid()}", flush=True)
            os._exit(137)
        return result

    frappe.db.commit = commit_then_crash
    frappe.set_user("synora-p1-buyer@dev.localhost")
    execution.execute_purchase_order(
        action_id,
        expected_digest,
        idempotency_key,
        correlation_id,
    )


def snapshot(item_code: str, action_id: str) -> None:
    """Print a redacted read-only ERP/governance fact snapshot."""

    frappe.set_user("Administrator")
    po_items = frappe.get_all(
        "Purchase Order Item",
        filters={"item_code": item_code},
        fields=["parent"],
        order_by="parent asc",
        limit=100,
        ignore_permissions=True,
    )
    po_names = sorted({str(row.parent) for row in po_items})
    po_docs = []
    for name in po_names:
        doc = frappe.get_doc("Purchase Order", name)
        po_docs.append(
            {
                "name": str(doc.name),
                "docstatus": int(doc.docstatus),
                "supplier": str(doc.supplier),
                "company": str(doc.company),
                "item_code": str(doc.items[0].item_code) if doc.items else None,
                "qty": str(doc.items[0].qty) if doc.items else None,
                "rate": str(doc.items[0].rate) if doc.items else None,
                "amount": str(doc.items[0].amount) if doc.items else None,
            }
        )
    action = frappe.get_doc("Synora Proposed Action", action_id)
    run = frappe.get_doc("Synora Agent Run", action.run)
    reservations = frappe.get_all(
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
        limit=1,
        ignore_permissions=True,
    )
    reservation = reservations[0] if reservations else None
    receipt = None
    if reservation and reservation.receipt:
        receipt_doc = frappe.get_doc("Synora Execution Receipt", reservation.receipt)
        receipt = {
            "receipt_id": str(receipt_doc.receipt_id),
            "final_state": str(receipt_doc.final_state),
            "response_category": str(receipt_doc.response_category),
            "failure_category": str(receipt_doc.failure_category or "") or None,
            "target_name": str(receipt_doc.target_name or "") or None,
        }
    print(
        "P6_SNAPSHOT "
        + json.dumps(
            {
                "item_code": item_code,
                "action_id": action_id,
                "po_count": len(po_docs),
                "po_docs": po_docs,
                "action_state": str(action.state),
                "run_state": str(run.run_state),
                "reservation": dict(reservation) if reservation else None,
                "receipt": receipt,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
