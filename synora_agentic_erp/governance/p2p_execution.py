"""Governed P2P document actions for Phase 10.

Every function in this module receives only a stored, approved action.  ERPNext
controllers remain the sole business writer; this module supplies typed input,
separation-of-duties checks, a durable reservation, and a read-back Receipt.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
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
    p2p_receipt_evidence_matches,
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
PARENT_SOURCE_ACTIONS = {
    "CREATE_PAYMENT_ENTRY_DRAFT": "Purchase Invoice",
    "SUBMIT_PO": "Purchase Order",
    "CANCEL_PO": "Purchase Order",
    "SUBMIT_PR": "Purchase Receipt",
    "CANCEL_PR": "Purchase Receipt",
    "SUBMIT_PI": "Purchase Invoice",
    "CANCEL_PI": "Purchase Invoice",
    "SUBMIT_PAYMENT_ENTRY": "Payment Entry",
    "CANCEL_PAYMENT_ENTRY": "Payment Entry",
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
PI_STATUSES = frozenset({"Unpaid", "Partly Paid", "Paid", "Overdue"})


def _financial_decimal(value: object, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ReadBackMismatch(f"{field} is not numeric") from error
    if not number.is_finite():
        raise ReadBackMismatch(f"{field} is not finite")
    return number


def _financial_text(value: object, field: str) -> str:
    text = str(value or "")
    if not text:
        raise ReadBackMismatch(f"{field} is missing")
    return text


def _financial_number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def p2p_action_calculation(action: Any) -> dict[str, Any]:
    """Build safe approval-display amounts from current ERP-readable fields."""

    payload = action.payload
    if action.action_type.startswith("CANCEL_"):
        target = TARGET_DOCTYPES[action.action_type]
        rows = frappe.get_list(
            target,
            filters={"name": payload["source_name"], "company": payload["company"]},
            fields=["docstatus", "status"],
            user=frappe.session.user,
            limit=1,
        )
        if not rows:
            return {
                "status": "未知",
                "accounting_state": "当前 ERP 单据不可见, 审批前必须重新读取",
                "basis": "当前 ERP 单据不可见",
            }
        return {
            "status": str(getattr(rows[0], "status", "") or "未知"),
            "docstatus": int(getattr(rows[0], "docstatus", 0) or 0),
            "accounting_state": (
                "取消仅由 ERP 原生 controller 执行; 下游依赖和库存/会计逆向结果以回执为准"
            ),
            "basis": f"读取当前 {target}; 不级联取消、不执行数据库回滚",
        }
    if action.action_type == "CREATE_PI_DRAFT":
        total = Decimal("0")
        line_amounts: list[str] = []
        for item in payload["items"]:
            amount = _financial_decimal(item["qty"], "qty") * _financial_decimal(
                item["rate"], "rate"
            )
            line_amounts.append(_financial_number(amount))
            total += amount
        source = frappe.get_list(
            "Purchase Receipt",
            filters={"name": payload["source_name"], "company": payload["company"]},
            fields=["currency"],
            user=frappe.session.user,
            limit=1,
        )
        currency = str(getattr(source[0], "currency", "") or "") if source else ""
        return {
            "currency": currency or "—",
            "line_amounts": line_amounts,
            "total_amount": _financial_number(total),
            "tax_amount": "—",
            "grand_total": "—",
            "outstanding_amount": "—",
            "status": "Draft",
            "accounting_state": "待 ERP controller 计算税额、科目和应付",
            "basis": "批准数量 x PR 费率; 税额、科目和总额由 ERPNext 计算",
        }
    if action.action_type == "SUBMIT_PI":
        rows = frappe.get_list(
            "Purchase Invoice",
            filters={"name": payload["source_name"], "company": payload["company"]},
            fields=[
                "currency",
                "total",
                "grand_total",
                "total_taxes_and_charges",
                "outstanding_amount",
                "status",
            ],
            user=frappe.session.user,
            limit=1,
        )
        if not rows:
            return {
                "currency": "—",
                "line_amounts": [],
                "total_amount": "—",
                "tax_amount": "—",
                "grand_total": "—",
                "outstanding_amount": "—",
                "status": "未知",
                "accounting_state": "当前 ERP 发票不可见, 审批前必须重新读取",
                "basis": "当前 ERP 单据不可见",
            }
        row = rows[0]
        total = _financial_decimal(getattr(row, "total", None), "total")
        grand_total = _financial_decimal(getattr(row, "grand_total", None), "grand_total")
        taxes = _financial_decimal(
            getattr(row, "total_taxes_and_charges", None), "total_taxes_and_charges"
        )
        outstanding = _financial_decimal(
            getattr(row, "outstanding_amount", None), "outstanding_amount"
        )
        return {
            "currency": str(getattr(row, "currency", "") or "—"),
            "line_amounts": [],
            "total_amount": _financial_number(total),
            "tax_amount": _financial_number(taxes),
            "grand_total": _financial_number(grand_total),
            "outstanding_amount": _financial_number(outstanding),
            "status": str(getattr(row, "status", "") or "未知"),
            "accounting_state": "提交前预览; 提交后以 ERP 会计回执为准",
            "basis": "读取当前 Purchase Invoice; ERP controller 负责最终税额、科目和应付",
        }
    if action.action_type == "CREATE_PAYMENT_ENTRY_DRAFT":
        rows = frappe.get_list(
            "Purchase Invoice",
            filters={"name": payload["source_name"], "company": payload["company"]},
            fields=["currency", "outstanding_amount", "status"],
            user=frappe.session.user,
            limit=1,
        )
        if not rows:
            return {
                "currency": "—",
                "line_amounts": [],
                "total_amount": "—",
                "tax_amount": "—",
                "grand_total": "—",
                "outstanding_amount": "—",
                "status": "未知",
                "accounting_state": "当前 ERP 发票不可见, 审批前必须重新读取",
                "basis": "当前 ERP 单据不可见",
            }
        source = rows[0]
        paid = _financial_decimal(payload["paid_amount"], "paid_amount")
        outstanding = _financial_decimal(
            getattr(source, "outstanding_amount", None), "outstanding_amount"
        )
        return {
            "currency": str(getattr(source, "currency", "") or "—"),
            "line_amounts": [_financial_number(paid)],
            "total_amount": _financial_number(paid),
            "tax_amount": "—",
            "grand_total": _financial_number(paid),
            "outstanding_amount": _financial_number(outstanding),
            "status": "Draft",
            "accounting_state": "待 ERP controller 生成 Payment Entry GL 并更新发票余额",
            "basis": "付款金额受当前 Purchase Invoice outstanding 限制; 不执行银行转账",
        }
    if action.action_type == "SUBMIT_PAYMENT_ENTRY":
        rows = frappe.get_list(
            "Payment Entry",
            filters={"name": payload["source_name"], "company": payload["company"]},
            fields=[
                "paid_to_account_currency",
                "paid_amount",
                "received_amount",
                "total_allocated_amount",
                "unallocated_amount",
                "status",
            ],
            user=frappe.session.user,
            limit=1,
        )
        if not rows:
            return {
                "currency": "—",
                "line_amounts": [],
                "total_amount": "—",
                "tax_amount": "—",
                "grand_total": "—",
                "outstanding_amount": "—",
                "status": "未知",
                "accounting_state": "当前 Payment Entry 不可见, 审批前必须重新读取",
                "basis": "当前 ERP 单据不可见",
            }
        row = rows[0]
        paid = _financial_decimal(getattr(row, "paid_amount", None), "paid_amount")
        received = _financial_decimal(getattr(row, "received_amount", None), "received_amount")
        allocated = _financial_decimal(
            getattr(row, "total_allocated_amount", None), "total_allocated_amount"
        )
        unallocated = _financial_decimal(
            getattr(row, "unallocated_amount", None), "unallocated_amount"
        )
        return {
            "currency": str(getattr(row, "paid_to_account_currency", "") or "—"),
            "line_amounts": [_financial_number(paid)],
            "total_amount": _financial_number(paid),
            "tax_amount": "—",
            "grand_total": _financial_number(received),
            "outstanding_amount": _financial_number(unallocated),
            "status": str(getattr(row, "status", "") or "Draft"),
            "accounting_state": "提交后以 Payment Entry GL 与发票 outstanding 回读为准",
            "basis": (
                f"Payment Entry 分配金额 {_financial_number(allocated)}; ERP controller 负责记账"
            ),
        }
    raise GatewayFault("INVALID_INPUT", "action does not have a P2P calculation", 400)


def _payment_entry_read_back(action: Any, doc: Any, verified: dict[str, Any]) -> dict[str, Any]:
    """Read back Payment Entry accounting and the invoice it settles."""

    status = _financial_text(getattr(doc, "status", ""), "status")
    if status != "Submitted":
        raise ReadBackMismatch("Payment Entry status is not Submitted")
    if str(getattr(doc, "party_type", "") or "") != "Supplier":
        raise ReadBackMismatch("Payment Entry party type is not Supplier")
    if str(getattr(doc, "payment_type", "") or "") != "Pay":
        raise ReadBackMismatch("Payment Entry type is not Pay")
    paid = _financial_decimal(getattr(doc, "paid_amount", None), "paid_amount")
    received = _financial_decimal(getattr(doc, "received_amount", None), "received_amount")
    allocated_total = _financial_decimal(
        getattr(doc, "total_allocated_amount", None), "total_allocated_amount"
    )
    unallocated = _financial_decimal(getattr(doc, "unallocated_amount", None), "unallocated_amount")
    if paid <= 0 or received <= 0 or received != paid or allocated_total != paid:
        raise ReadBackMismatch("Payment Entry amounts are inconsistent")
    if unallocated < 0 or unallocated > paid:
        raise ReadBackMismatch("Payment Entry unallocated amount is outside valid bounds")

    references = list(getattr(doc, "references", []) or [])
    if len(references) != 1:
        raise ReadBackMismatch("Payment Entry must contain one invoice reference")
    reference = references[0]
    if str(getattr(reference, "reference_doctype", "") or "") != "Purchase Invoice":
        raise ReadBackMismatch("Payment Entry reference is not a Purchase Invoice")
    reference_name = _financial_text(getattr(reference, "reference_name", ""), "reference_name")
    reference_allocated = _financial_decimal(
        getattr(reference, "allocated_amount", None), "reference_allocated_amount"
    )
    if reference_allocated != paid:
        raise ReadBackMismatch("Payment Entry reference allocation does not match paid amount")
    invoice_rows = frappe.get_list(
        "Purchase Invoice",
        filters={"name": reference_name, "company": getattr(doc, "company", "")},
        fields=["name", "supplier", "status", "grand_total", "outstanding_amount", "docstatus"],
        user=frappe.session.user,
        limit=1,
    )
    if not invoice_rows:
        raise ReadBackMismatch("settled Purchase Invoice is outside current ERP scope")
    invoice = invoice_rows[0]
    if int(getattr(invoice, "docstatus", 0) or 0) != 1:
        raise ReadBackMismatch("settled Purchase Invoice is not submitted")
    if str(getattr(invoice, "supplier", "") or "") != str(getattr(doc, "party", "") or ""):
        raise ReadBackMismatch("Payment Entry party and invoice supplier differ")
    invoice_status = _financial_text(getattr(invoice, "status", ""), "invoice.status")
    if invoice_status not in PI_STATUSES:
        raise ReadBackMismatch("settled Purchase Invoice status is invalid")
    invoice_total = _financial_decimal(getattr(invoice, "grand_total", None), "invoice.grand_total")
    invoice_outstanding = _financial_decimal(
        getattr(invoice, "outstanding_amount", None), "invoice.outstanding_amount"
    )
    if invoice_total <= 0 or invoice_outstanding < 0 or invoice_outstanding > invoice_total:
        raise ReadBackMismatch("settled Purchase Invoice outstanding amount is invalid")
    if invoice_status == "Paid" and invoice_outstanding != 0:
        raise ReadBackMismatch("Paid Purchase Invoice has a non-zero outstanding amount")
    if invoice_status in {"Unpaid", "Overdue"} and invoice_outstanding <= 0:
        raise ReadBackMismatch("unpaid Purchase Invoice has no outstanding amount")
    if invoice_status == "Partly Paid" and not 0 < invoice_outstanding < invoice_total:
        raise ReadBackMismatch(
            "partly paid Purchase Invoice has an inconsistent outstanding amount"
        )

    gl_rows = frappe.db.sql(
        """
        SELECT COUNT(*), COALESCE(SUM(debit), 0), COALESCE(SUM(credit), 0)
        FROM `tabGL Entry`
        WHERE voucher_type = 'Payment Entry'
          AND voucher_no = %s
          AND is_cancelled = 0
        """,
        (str(getattr(doc, "name", "") or ""),),
    )
    gl_count = int(gl_rows[0][0] or 0)
    gl_debit = _financial_decimal(gl_rows[0][1], "gl_debit_total")
    gl_credit = _financial_decimal(gl_rows[0][2], "gl_credit_total")
    if gl_count <= 0 or abs(gl_debit - gl_credit) > Decimal("0.00000001"):
        raise ReadBackMismatch("Payment Entry GL entries are missing or unbalanced")
    verified.update(
        {
            "status": status,
            "party_type": "Supplier",
            "payment_type": "Pay",
            "paid_amount": _financial_number(paid),
            "received_amount": _financial_number(received),
            "total_allocated_amount": _financial_number(allocated_total),
            "unallocated_amount": _financial_number(unallocated),
            "reference_doctype": "Purchase Invoice",
            "reference_name": reference_name,
            "reference_allocated_amount": _financial_number(reference_allocated),
            "purchase_invoice_status": invoice_status,
            "purchase_invoice_grand_total": _financial_number(invoice_total),
            "purchase_invoice_outstanding_amount": _financial_number(invoice_outstanding),
            "gl_entry_count": gl_count,
            "gl_debit_total": _financial_number(gl_debit),
            "gl_credit_total": _financial_number(gl_credit),
        }
    )
    return verified


def _ledger_totals(
    voucher_type: str, voucher_no: str, table: str, amount_field: str
) -> tuple[int, Decimal]:
    """Return all ledger rows and their signed net for a cancelled voucher."""

    rows = frappe.db.sql(
        f"""
        SELECT COUNT(*), COALESCE(SUM({amount_field}), 0)
        FROM `{table}`
        WHERE voucher_type = %s AND voucher_no = %s
        """,
        (voucher_type, voucher_no),
    )
    return int(rows[0][0] or 0), _financial_decimal(rows[0][1], f"{table}.net_{amount_field}")


def _cancel_purchase_order_read_back(doc: Any, verified: dict[str, Any]) -> dict[str, Any]:
    status = _financial_text(getattr(doc, "status", ""), "status")
    if status != "Cancelled":
        raise ReadBackMismatch("Purchase Order status is not Cancelled")
    per_received = _financial_decimal(getattr(doc, "per_received", 0), "per_received")
    per_billed = _financial_decimal(getattr(doc, "per_billed", 0), "per_billed")
    if per_received != 0 or per_billed != 0:
        raise ReadBackMismatch("cancelled Purchase Order still has received or billed progress")
    verified.update(
        {
            "status": status,
            "per_received": _financial_number(per_received),
            "per_billed": _financial_number(per_billed),
        }
    )
    return verified


def _cancel_purchase_receipt_read_back(doc: Any, verified: dict[str, Any]) -> dict[str, Any]:
    status = _financial_text(getattr(doc, "status", ""), "status")
    if status != "Cancelled":
        raise ReadBackMismatch("Purchase Receipt status is not Cancelled")

    stock_item_seen = False
    for index, item in enumerate(getattr(doc, "items", []) or []):
        po_name = str(getattr(item, "purchase_order", "") or "")
        po_item_name = str(getattr(item, "purchase_order_item", "") or "")
        if not po_name or not po_item_name:
            continue
        po_item = frappe.db.get_value(
            "Purchase Order Item", po_item_name, ["parent", "received_qty"], as_dict=True
        )
        if not po_item or str(po_item.parent) != po_name:
            raise ReadBackMismatch(f"item_{index} Purchase Order source is inconsistent")
        remaining_rows = frappe.db.sql(
            """
            SELECT COALESCE(SUM(pri.qty), 0)
            FROM `tabPurchase Receipt Item` pri
            INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
            WHERE pri.purchase_order = %s
              AND pri.purchase_order_item = %s
              AND pr.docstatus = 1
            """,
            (po_name, po_item_name),
        )
        expected_received = _financial_decimal(remaining_rows[0][0], "expected_received_qty")
        actual_received = _financial_decimal(po_item.received_qty, "po_received_qty")
        if actual_received != expected_received:
            raise ReadBackMismatch(
                f"item_{index} Purchase Order received quantity is inconsistent"
            )
        po_progress = frappe.db.get_value(
            "Purchase Order", po_name, ["company", "per_received"], as_dict=True
        )
        if not po_progress or str(po_progress.company) != str(getattr(doc, "company", "") or ""):
            raise ReadBackMismatch(f"item_{index} Purchase Order is outside the receipt company")
        per_received = _financial_decimal(po_progress.per_received, "po_per_received")
        if per_received < 0 or per_received > 100:
            raise ReadBackMismatch(f"item_{index} Purchase Order progress is invalid")
        verified[f"item_{index}.purchase_order"] = po_name
        verified[f"item_{index}.purchase_order_item"] = po_item_name
        verified[f"item_{index}.po_received_qty"] = _financial_number(actual_received)
        verified[f"item_{index}.po_per_received"] = _financial_number(per_received)
        stock_item_seen = stock_item_seen or bool(
            frappe.db.get_value("Item", getattr(item, "item_code", ""), "is_stock_item")
        )

    if stock_item_seen:
        count, net = _ledger_totals(
            "Purchase Receipt",
            str(getattr(doc, "name", "") or ""),
            "tabStock Ledger Entry",
            "actual_qty",
        )
        if count < 2 or net != 0:
            raise ReadBackMismatch("cancelled Purchase Receipt stock ledger is not reversed")
        verified["stock_ledger_entry_count"] = count
        verified["stock_actual_qty_net"] = _financial_number(net)
    verified["status"] = status
    return verified


def _cancel_purchase_invoice_read_back(doc: Any, verified: dict[str, Any]) -> dict[str, Any]:
    status = _financial_text(getattr(doc, "status", ""), "status")
    if status != "Cancelled":
        raise ReadBackMismatch("Purchase Invoice status is not Cancelled")
    grand_total = _financial_decimal(getattr(doc, "grand_total", 0), "grand_total")
    outstanding = _financial_decimal(getattr(doc, "outstanding_amount", 0), "outstanding_amount")
    if grand_total < 0 or outstanding < 0:
        raise ReadBackMismatch("cancelled Purchase Invoice accounting totals are invalid")

    gl_count, gl_net = _ledger_totals(
        "Purchase Invoice", str(getattr(doc, "name", "") or ""), "tabGL Entry", "debit - credit"
    )
    if gl_count < 2 or gl_net != 0:
        raise ReadBackMismatch("cancelled Purchase Invoice GL entries are not reversed")
    for index, item in enumerate(getattr(doc, "items", []) or []):
        pr_name = str(getattr(item, "purchase_receipt", "") or "")
        if pr_name:
            pr = frappe.db.get_value(
                "Purchase Receipt", pr_name, ["company", "per_billed"], as_dict=True
            )
            if not pr or str(pr.company) != str(getattr(doc, "company", "") or ""):
                raise ReadBackMismatch(f"item_{index} Purchase Receipt is outside invoice company")
            pr_per_billed = _financial_decimal(pr.per_billed, "pr_per_billed")
            if pr_per_billed < 0 or pr_per_billed > 100:
                raise ReadBackMismatch(f"item_{index} Purchase Receipt billing progress is invalid")
            verified[f"item_{index}.purchase_receipt"] = pr_name
            verified[f"item_{index}.pr_per_billed"] = _financial_number(pr_per_billed)
        po_name = str(getattr(item, "purchase_order", "") or "")
        if not po_name and pr_name and getattr(item, "pr_detail", None):
            po_name = str(
                frappe.db.get_value("Purchase Receipt Item", item.pr_detail, "purchase_order") or ""
            )
        if po_name:
            po = frappe.db.get_value(
                "Purchase Order", po_name, ["company", "per_billed"], as_dict=True
            )
            if not po or str(po.company) != str(getattr(doc, "company", "") or ""):
                raise ReadBackMismatch(f"item_{index} Purchase Order is outside invoice company")
            po_per_billed = _financial_decimal(po.per_billed, "po_per_billed")
            if po_per_billed < 0 or po_per_billed > 100:
                raise ReadBackMismatch(f"item_{index} Purchase Order billing progress is invalid")
            verified[f"item_{index}.purchase_order"] = po_name
            verified[f"item_{index}.po_per_billed"] = _financial_number(po_per_billed)
    verified.update(
        {
            "status": status,
            "grand_total": _financial_number(grand_total),
            "outstanding_amount": _financial_number(outstanding),
            "gl_entry_count": gl_count,
            "gl_net_debit_minus_credit": _financial_number(gl_net),
        }
    )
    return verified


def _cancel_payment_entry_read_back(doc: Any, verified: dict[str, Any]) -> dict[str, Any]:
    status = _financial_text(getattr(doc, "status", ""), "status")
    if status != "Cancelled":
        raise ReadBackMismatch("Payment Entry status is not Cancelled")
    references = list(getattr(doc, "references", []) or [])
    if len(references) != 1:
        raise ReadBackMismatch("cancelled Payment Entry must retain one invoice reference")
    reference = references[0]
    if str(getattr(reference, "reference_doctype", "") or "") != "Purchase Invoice":
        raise ReadBackMismatch("cancelled Payment Entry reference is not a Purchase Invoice")
    reference_name = _financial_text(getattr(reference, "reference_name", ""), "reference_name")
    paid = _financial_decimal(getattr(doc, "paid_amount", 0), "paid_amount")
    allocated = _financial_decimal(getattr(reference, "allocated_amount", 0), "allocated_amount")
    if paid <= 0 or allocated != paid:
        raise ReadBackMismatch("cancelled Payment Entry allocation is invalid")

    invoice = frappe.db.get_value(
        "Purchase Invoice",
        reference_name,
        ["company", "status", "docstatus", "grand_total", "outstanding_amount"],
        as_dict=True,
    )
    if not invoice or str(invoice.company) != str(getattr(doc, "company", "") or ""):
        raise ReadBackMismatch("cancelled Payment Entry invoice is outside current company")
    invoice_status = _financial_text(invoice.status, "purchase_invoice.status")
    invoice_outstanding = _financial_decimal(
        invoice.outstanding_amount, "purchase_invoice.outstanding_amount"
    )
    invoice_total = _financial_decimal(invoice.grand_total, "purchase_invoice.grand_total")
    if invoice_total < 0 or invoice_outstanding < 0:
        raise ReadBackMismatch("cancelled Payment Entry invoice outstanding is invalid")
    gl_count, gl_net = _ledger_totals(
        "Payment Entry", str(getattr(doc, "name", "") or ""), "tabGL Entry", "debit - credit"
    )
    if gl_count < 2 or gl_net != 0:
        raise ReadBackMismatch("cancelled Payment Entry GL entries are not reversed")
    verified.update(
        {
            "status": status,
            "paid_amount": _financial_number(paid),
            "reference_name": reference_name,
            "reference_allocated_amount": _financial_number(allocated),
            "purchase_invoice_status": invoice_status,
            "purchase_invoice_docstatus": int(invoice.docstatus or 0),
            "purchase_invoice_grand_total": _financial_number(invoice_total),
            "purchase_invoice_outstanding_amount": _financial_number(invoice_outstanding),
            "gl_entry_count": gl_count,
            "gl_net_debit_minus_credit": _financial_number(gl_net),
        }
    )
    return verified


def p2p_read_back_with_financials(action: Any, doc: Any) -> dict[str, Any]:
    """Read back P2P fields plus authoritative financial evidence."""

    verified = p2p_read_back(action, doc)
    if action.action_type == "CANCEL_PO":
        return _cancel_purchase_order_read_back(doc, verified)
    if action.action_type == "CANCEL_PR":
        return _cancel_purchase_receipt_read_back(doc, verified)
    if action.action_type == "CANCEL_PI":
        return _cancel_purchase_invoice_read_back(doc, verified)
    if action.action_type == "CANCEL_PAYMENT_ENTRY":
        return _cancel_payment_entry_read_back(doc, verified)
    if action.action_type == "SUBMIT_PAYMENT_ENTRY":
        return _payment_entry_read_back(action, doc, verified)
    if action.action_type != "SUBMIT_PI":
        return verified

    status = _financial_text(getattr(doc, "status", ""), "status")
    if status not in PI_STATUSES:
        raise ReadBackMismatch("Purchase Invoice status is not a settled ERP status")
    currency = _financial_text(getattr(doc, "currency", ""), "currency")
    total = _financial_decimal(getattr(doc, "total", None), "total")
    grand_total = _financial_decimal(getattr(doc, "grand_total", None), "grand_total")
    taxes = _financial_decimal(
        getattr(doc, "total_taxes_and_charges", None), "total_taxes_and_charges"
    )
    outstanding = _financial_decimal(getattr(doc, "outstanding_amount", None), "outstanding_amount")
    if grand_total <= 0 or outstanding < 0 or outstanding > grand_total:
        raise ReadBackMismatch("Purchase Invoice accounting totals are outside valid bounds")
    if status == "Paid" and outstanding != 0:
        raise ReadBackMismatch("Paid Purchase Invoice has a non-zero outstanding amount")
    if status in {"Unpaid", "Overdue"} and outstanding <= 0:
        raise ReadBackMismatch("unpaid Purchase Invoice has no outstanding amount")
    if status == "Partly Paid" and not 0 < outstanding < grand_total:
        raise ReadBackMismatch(
            "partly paid Purchase Invoice has an inconsistent outstanding amount"
        )

    gl_rows = frappe.db.sql(
        """
        SELECT COUNT(*), COALESCE(SUM(debit), 0), COALESCE(SUM(credit), 0)
        FROM `tabGL Entry`
        WHERE voucher_type = 'Purchase Invoice'
          AND voucher_no = %s
          AND is_cancelled = 0
        """,
        (str(getattr(doc, "name", "") or ""),),
    )
    gl_count = int(gl_rows[0][0] or 0)
    gl_debit = _financial_decimal(gl_rows[0][1], "gl_debit_total")
    gl_credit = _financial_decimal(gl_rows[0][2], "gl_credit_total")
    if gl_count <= 0 or abs(gl_debit - gl_credit) > Decimal("0.00000001"):
        raise ReadBackMismatch("Purchase Invoice GL entries are missing or unbalanced")

    for index, row in enumerate(getattr(doc, "items", []) or []):
        pr_name = _financial_text(
            getattr(row, "purchase_receipt", ""), f"item_{index}.purchase_receipt"
        )
        pr_detail = _financial_text(getattr(row, "pr_detail", ""), f"item_{index}.pr_detail")
        pr_item = frappe.db.get_value(
            "Purchase Receipt Item",
            pr_detail,
            ["parent", "purchase_order", "purchase_order_item", "billed_amt"],
            as_dict=True,
        )
        if not pr_item or str(pr_item.parent) != pr_name:
            raise ReadBackMismatch(f"item_{index} Purchase Receipt source is inconsistent")
        pr = frappe.db.get_value(
            "Purchase Receipt", pr_name, ["company", "per_billed"], as_dict=True
        )
        if not pr or str(pr.company) != str(getattr(doc, "company", "") or ""):
            raise ReadBackMismatch(f"item_{index} Purchase Receipt is outside the invoice company")
        pr_billed_amt = _financial_decimal(pr_item.billed_amt, f"item_{index}.pr_billed_amt")
        pr_per_billed = _financial_decimal(pr.per_billed, f"item_{index}.pr_per_billed")
        if pr_billed_amt < 0 or pr_per_billed < 0 or pr_per_billed > 100:
            raise ReadBackMismatch(f"item_{index} Purchase Receipt billing result is invalid")
        po_name = str(getattr(row, "purchase_order", "") or "") or str(
            getattr(pr_item, "purchase_order", "") or ""
        )
        po_per_billed: Decimal | None = None
        if po_name:
            po = frappe.db.get_value(
                "Purchase Order", po_name, ["company", "per_billed"], as_dict=True
            )
            if not po or str(po.company) != str(getattr(doc, "company", "") or ""):
                raise ReadBackMismatch(
                    f"item_{index} Purchase Order is outside the invoice company"
                )
            po_per_billed = _financial_decimal(po.per_billed, f"item_{index}.po_per_billed")
            if po_per_billed < 0 or po_per_billed > 100:
                raise ReadBackMismatch(f"item_{index} Purchase Order billing result is invalid")
        verified[f"item_{index}.pr_billed_amt"] = _financial_number(pr_billed_amt)
        verified[f"item_{index}.pr_per_billed"] = _financial_number(pr_per_billed)
        verified[f"item_{index}.purchase_receipt"] = pr_name
        verified[f"item_{index}.pr_detail"] = pr_detail
        verified[f"item_{index}.purchase_order"] = po_name
        if po_per_billed is not None:
            verified[f"item_{index}.po_per_billed"] = _financial_number(po_per_billed)

    verified.update(
        {
            "status": status,
            "currency": currency,
            "total": _financial_number(total),
            "grand_total": _financial_number(grand_total),
            "total_taxes_and_charges": _financial_number(taxes),
            "outstanding_amount": _financial_number(outstanding),
            "gl_entry_count": gl_count,
            "gl_debit_total": _financial_number(gl_debit),
            "gl_credit_total": _financial_number(gl_credit),
        }
    )
    return verified


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
        if str(getattr(row, "uom", "") or "") != item["uom"]:
            raise GatewayFault(
                "CONFLICT", "mapped invoice UOM differs from the approved source row", 409
            )
        if str(getattr(row, "warehouse", "") or "") != item["warehouse"]:
            raise GatewayFault(
                "CONFLICT", "mapped invoice warehouse differs from the approved source row", 409
            )
    target.posting_date = action.payload["transaction_date"]
    return target


def _build_payment_entry(action: Any) -> Any:
    """Build a payment through ERPNext's Purchase Invoice mapper."""

    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    payload = action.payload
    paid_amount = Decimal(str(payload["paid_amount"]))
    received_amount = Decimal(str(payload["received_amount"]))
    target = get_payment_entry(
        "Purchase Invoice",
        payload["source_name"],
        party_amount=paid_amount,
        bank_account=payload["paid_from"],
        party_type=payload["party_type"],
        payment_type=payload["payment_type"],
        reference_date=payload["posting_date"],
    )
    for field in (
        "company",
        "party_type",
        "party",
        "payment_type",
        "paid_from",
        "paid_to",
    ):
        if str(getattr(target, field, "") or "") != str(payload[field]):
            raise GatewayFault(
                "CONFLICT", f"mapped Payment Entry {field} differs from approval", 409
            )

    references = payload["references"]
    if len(references) != 1:
        raise GatewayFault(
            "CONFLICT", "Payment Entry must contain one approved invoice reference", 409
        )
    reference = references[0]
    target.set("references", [])
    target.append(
        "references",
        {
            "reference_doctype": reference["reference_doctype"],
            "reference_name": reference["reference_name"],
            "allocated_amount": Decimal(str(reference["allocated_amount"])),
        },
    )
    target.posting_date = payload["posting_date"]
    target.paid_amount = paid_amount
    target.received_amount = received_amount
    target.set_missing_ref_details(force=True)
    target.set_amounts()
    target.paid_amount = paid_amount
    target.received_amount = received_amount
    return target


def _build_target(action: Any) -> Any:
    if action.action_type == "CREATE_PR_DRAFT":
        return _build_pr(action)
    if action.action_type == "CREATE_PI_DRAFT":
        return _build_pi(action)
    if action.action_type == "CREATE_PAYMENT_ENTRY_DRAFT":
        return _build_payment_entry(action)
    raise GatewayFault("INVALID_INPUT", "action does not create a P2P draft", 400)


def _load_source_target(action: Any) -> Any:
    is_submit = action.action_type.startswith("SUBMIT_")
    return _load_target(action, expected_docstatus=0 if is_submit else 1)


def _release_p2p_source_locks(lock_names: list[str]) -> None:
    """Release connection-scoped source locks without hiding the primary error."""

    for lock_name in reversed(lock_names):
        try:
            frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_name,))
        except Exception:
            # A connection close also releases named locks.  Cleanup must not
            # replace an ERP/controller failure with a secondary DB error.
            continue


def _lock_p2p_source(action: Any) -> list[str]:
    """Serialize competing draft conversions on the reviewed source row.

    Frappe test and worker connections keep autocommit enabled while a
    controller call is in progress, so a row-level ``FOR UPDATE`` alone does
    not provide a reliable reservation boundary.  MariaDB named locks are
    connection-scoped and remain held across the explicit governance commits;
    the lock name is hashed to a bounded, non-sensitive value.
    """

    child_doctype = SOURCE_CHILD_DOCTYPES.get(str(action.action_type))
    parent_doctype = PARENT_SOURCE_ACTIONS.get(str(action.action_type))
    if child_doctype is None and parent_doctype is None:
        return []
    payload = action.payload
    lock_specs: list[tuple[str, str, str | None]] = []
    if child_doctype is not None:
        lock_specs.extend(
            (child_doctype, item["source_row"], payload["source_name"])
            for item in sorted(payload["items"], key=lambda value: value["source_row"])
        )
    else:
        lock_specs.append((parent_doctype or "", payload["source_name"], None))
    acquired: list[str] = []
    try:
        for lock_doctype, source_name, parent_name in lock_specs:
            lock_name = (
                "synora-p2p-"
                + hashlib.sha256(
                    f"{lock_doctype}:{source_name}:{parent_name or ''}".encode()
                ).hexdigest()[:52]
            )
            result = frappe.db.sql("SELECT GET_LOCK(%s, %s)", (lock_name, 30))
            if not result or int(result[0][0] or 0) != 1:
                raise GatewayFault("CONFLICT", "source row is busy with another P2P action", 409)
            acquired.append(lock_name)

            # Keep the existing current-row check as evidence that the named
            # lock is bound to the reviewed parent/child identity.
            table = f"tab{lock_doctype}"
            where = "name = %s"
            params: tuple[str, ...] = (source_name,)
            if parent_name is not None:
                where += " AND parent = %s"
                params += (parent_name,)
            rows = frappe.db.sql(
                f"""
                SELECT name
                FROM `{table}`
                WHERE {where}
                FOR UPDATE
                """,
                params,
                as_dict=True,
            )
            if not rows:
                raise GatewayFault("CONFLICT", "reviewed source row is no longer available", 409)
    except Exception:
        _release_p2p_source_locks(acquired)
        raise
    return acquired


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
    verified = p2p_read_back_with_financials(action, target)
    recorded = json.loads(receipt_doc.verified_fields_json)
    if not p2p_receipt_evidence_matches(action, recorded, verified):
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
            target_doctype = TARGET_DOCTYPES[action.action_type]
            if target_name and frappe.db.exists(target_doctype, target_name):
                # Re-read after rollback.  A native controller can commit a
                # business cancellation before a surrounding response or
                # read-back failure; the recovery record must state what the
                # database actually shows rather than trusting the in-memory
                # object from the failed call.
                try:
                    persisted_target = frappe.get_doc(target_doctype, target_name)
                except Exception:
                    persisted_target = target
            else:
                persisted_target = None
        final_state = "RECONCILIATION_REQUIRED" if uncertain else "FAILED"
        recovery_evidence = None
        if uncertain or action.action_type.startswith("CANCEL_"):
            observed_docstatus = (
                int(getattr(persisted_target, "docstatus", 0) or 0)
                if persisted_target is not None
                else None
            )
            expected_docstatus = 2 if action.action_type.startswith("CANCEL_") else 1
            side_effect_state = (
                "APPLIED_BUT_RESPONSE_FAILED"
                if observed_docstatus == expected_docstatus and uncertain
                else "NOT_APPLIED"
                if not uncertain
                else "UNKNOWN"
            )
            recovery_evidence = {
                "operation": "cancel" if action.action_type.startswith("CANCEL_") else "p2p_write",
                "side_effect_state": side_effect_state,
                "target_observed": persisted_target is not None,
                "target_docstatus": observed_docstatus,
                "manual_intervention": bool(uncertain),
                "recovery_action": (
                    "read_only_reconcile_then_manual_intervention"
                    if uncertain
                    else "inspect_failure_and_repropose_after_state_recheck"
                ),
                "reason": failure,
            }
        stored = _persist_receipt(
            action,
            run,
            reservation,
            final_state=final_state,
            response_category="UNCERTAIN_RESULT" if uncertain else category,
            failure_category=failure,
            target=persisted_target,
            verified_fields={},
            evidence=recovery_evidence,
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
    source_locks: list[str] = []
    try:
        # Acquire source-row locks before the first post-reservation read.  A
        # waiting connection must establish its read snapshot only after the
        # winner has committed its draft, otherwise REPEATABLE READ could
        # revalidate against stale remaining quantities.
        source_locks = _lock_p2p_source(action)
        pre_execute_recheck(safe_action_id, safe_digest, safe_key)
        run = _lock_run_for_action(safe_action_id)
        action_doc, action, locked = _lock_action(safe_action_id)
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
        verified = p2p_read_back_with_financials(action, target)
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
    finally:
        _release_p2p_source_locks(source_locks)


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
            verified = p2p_read_back_with_financials(action, target)
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
        None if reconciled else str(reservation.failure_category or "AMBIGUOUS_P2P_RESULT")
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
