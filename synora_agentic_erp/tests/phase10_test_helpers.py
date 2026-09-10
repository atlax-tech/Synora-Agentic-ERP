"""Cleanup helpers for committed Phase 10 integration fixtures."""

from __future__ import annotations

import frappe


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
        receipt = frappe.get_doc("Purchase Receipt", name)
        if int(receipt.docstatus or 0) == 1:
            receipt.cancel()
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
        order = frappe.get_doc("Purchase Order", name)
        if int(order.docstatus or 0) == 1:
            order.cancel()
    frappe.db.commit()
