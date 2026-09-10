"""Cleanup helpers for committed Phase 10 integration fixtures."""

from __future__ import annotations

import frappe


def _cancel_if_submitted(doctype: str, name: str) -> None:
    """Cancel only a currently submitted document, using a fresh DB status."""

    if int(frappe.db.get_value(doctype, name, "docstatus") or 0) != 1:
        return
    doc = frappe.get_doc(doctype, name)
    if int(doc.docstatus or 0) != 1:
        return
    try:
        doc.cancel()
    except frappe.TimestampMismatchError:
        # A previous controller cancellation can refresh linked documents in
        # the same transaction. Re-read before retrying; never hide a new
        # business error or issue a second cancel for an already-cancelled doc.
        if int(frappe.db.get_value(doctype, name, "docstatus") or 0) != 1:
            return
        frappe.get_doc(doctype, name).cancel()


def cancel_p10_test_documents() -> None:
    """Cancel generated P2P fixtures while preserving their ERP history."""

    item_codes = frappe.get_all(
        "Item",
        filters={"item_code": ["like", "SYNORA-P10-%"]},
        pluck="item_code",
        limit=1000,
    )
    if not item_codes:
        return
    invoice_names = sorted(
        {
            row.parent
            for row in frappe.get_all(
                "Purchase Invoice Item",
                filters={"item_code": ["in", item_codes]},
                fields=["parent"],
                limit=1000,
            )
        }
    )
    payment_names = (
        sorted(
            {
                row.parent
                for row in frappe.get_all(
                    "Payment Entry Reference",
                    filters={
                        "reference_doctype": "Purchase Invoice",
                        "reference_name": ["in", invoice_names],
                    },
                    fields=["parent"],
                    limit=1000,
                )
            }
        )
        if invoice_names
        else []
    )
    for name in payment_names:
        _cancel_if_submitted("Payment Entry", name)
    frappe.db.commit()
    for name in invoice_names:
        _cancel_if_submitted("Purchase Invoice", name)
    frappe.db.commit()
    receipt_names = sorted(
        {
            row.parent
            for row in frappe.get_all(
                "Purchase Receipt Item",
                filters={"item_code": ["in", item_codes]},
                fields=["parent"],
                limit=1000,
            )
        }
    )
    for name in receipt_names:
        _cancel_if_submitted("Purchase Receipt", name)
    frappe.db.commit()
    order_names = sorted(
        {
            row.parent
            for row in frappe.get_all(
                "Purchase Order Item",
                filters={"item_code": ["in", item_codes]},
                fields=["parent"],
                limit=1000,
            )
        }
    )
    for name in order_names:
        _cancel_if_submitted("Purchase Order", name)
    frappe.db.commit()
