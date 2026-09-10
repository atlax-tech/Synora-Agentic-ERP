"""Durable P2P PlanStep projection and Run-level orchestration guards.

The ERP documents, governed Actions, Reservations and Receipts remain the
business facts.  This small projection stores dependency edges and a bounded
step state so a Run can resume without replaying a completed side effect.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import frappe
from frappe.utils import now_datetime

from synora_agentic_erp.agent.service import _set_run_state
from synora_agentic_erp.agent.state_machine import validate_transition
from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.gateway.security import workflow_expired
from synora_agentic_erp.governance.contracts import (
    P2P_ACTION_TYPES,
    TARGET_DOCTYPES,
    build_proposed_action,
)
from synora_agentic_erp.governance.service import transition_action_state

P2P_STEP_SCHEMA_VERSION = "1"
P2P_GOAL_SCHEMA_VERSION = "1"
P2P_SETTLEMENT_ENDPOINT = "RECEIVED_BILLED_PAID"
P2P_STEP_SERVICE_FLAG = "synora_p2p_orchestration_service"
SUCCESS_RECEIPT_STATES = frozenset({"SUCCEEDED", "RECONCILED_SUCCESS"})
UNCERTAIN_RECEIPT_STATES = frozenset({"RECONCILIATION_REQUIRED", "MANUAL_INTERVENTION"})
FAILED_RECEIPT_STATES = frozenset({"FAILED", "RECONCILED_FAILURE"})
TERMINAL_STEP_STATES = frozenset(
    {
        "SUCCEEDED",
        "FAILED",
        "RECONCILIATION_REQUIRED",
        "REINVESTIGATION_REQUIRED",
        "CANCELLED",
        "EXPIRED",
    }
)
ACTION_ORDER = {
    "SUBMIT_PO": 10,
    "CREATE_PR_DRAFT": 20,
    "SUBMIT_PR": 30,
    "CREATE_PI_DRAFT": 40,
    "SUBMIT_PI": 50,
    "CREATE_PAYMENT_ENTRY_DRAFT": 60,
    "SUBMIT_PAYMENT_ENTRY": 70,
    "CANCEL_PAYMENT_ENTRY": 80,
    "CANCEL_PI": 90,
    "CANCEL_PR": 100,
    "CANCEL_PO": 110,
}


@dataclass(frozen=True)
class P2PPlanStepView:
    action_id: str
    step_id: str
    order: int
    state: str
    depends_on: tuple[str, ...]
    blocked_reason: str | None = None
    reinvestigation_required: bool = False


def _goal_json(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise GatewayFault("INVALID_INPUT", "P2P goal JSON is invalid", 400) from error
    return value


def _goal_text(value: object, field: str, maximum: int = 140) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise GatewayFault("INVALID_INPUT", f"P2P goal {field} is invalid", 400)
    return value


def _goal_decimal(value: object, field: str) -> str:
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, (str, int)):
        raise GatewayFault("INVALID_INPUT", f"P2P goal {field} is invalid", 400)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise GatewayFault("INVALID_INPUT", f"P2P goal {field} is invalid", 400) from error
    if not number.is_finite() or number <= 0:
        raise GatewayFault("INVALID_INPUT", f"P2P goal {field} is invalid", 400)
    return format(number.normalize(), "f")


def normalize_p2p_goal(value: object) -> dict[str, Any]:
    """Normalize the small, explicit business target used by a P2P Run.

    Natural-language Run goals are never parsed here.  The caller must submit
    the source Purchase Order and exact source rows after the initiator has
    reviewed the proposed scope.  Decimal values are serialized as strings so
    a browser or model cannot change precision by using a floating point value.
    """

    raw = _goal_json(value)
    if not isinstance(raw, dict):
        raise GatewayFault("INVALID_INPUT", "P2P goal must be an object", 400)
    allowed = {
        "schema_version",
        "source_doctype",
        "source_name",
        "source_rows",
        "settlement_endpoint",
    }
    if (
        set(raw) - allowed
        or not {"source_doctype", "source_name", "source_rows"}.issubset(raw)
        or raw.get("schema_version", P2P_GOAL_SCHEMA_VERSION) != P2P_GOAL_SCHEMA_VERSION
    ):
        raise GatewayFault("INVALID_INPUT", "P2P goal fields are invalid", 400)
    source_doctype = _goal_text(raw["source_doctype"], "source_doctype", 80)
    if source_doctype != "Purchase Order":
        raise GatewayFault("INVALID_INPUT", "P2P goal source_doctype is invalid", 400)
    source_name = _goal_text(raw["source_name"], "source_name")
    endpoint = raw.get("settlement_endpoint", P2P_SETTLEMENT_ENDPOINT)
    if endpoint != P2P_SETTLEMENT_ENDPOINT:
        raise GatewayFault("INVALID_INPUT", "P2P goal settlement endpoint is invalid", 400)
    rows = raw["source_rows"]
    if not isinstance(rows, list) or not rows or len(rows) > 100:
        raise GatewayFault("INVALID_INPUT", "P2P goal source_rows are invalid", 400)
    normalized_rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise GatewayFault("INVALID_INPUT", f"P2P goal source_rows[{index}] is invalid", 400)
        allowed_row = {"source_row", "item_code", "target_qty"}
        required_row = {"source_row", "item_code", "target_qty"}
        if set(row) - allowed_row or not required_row.issubset(row):
            raise GatewayFault("INVALID_INPUT", f"P2P goal source_rows[{index}] is invalid", 400)
        source_row = _goal_text(row["source_row"], "source_row")
        if source_row in seen:
            raise GatewayFault("INVALID_INPUT", "P2P goal source_rows contain duplicates", 400)
        seen.add(source_row)
        normalized: dict[str, str] = {
            "source_row": source_row,
            "item_code": _goal_text(row["item_code"], "item_code"),
            "target_qty": _goal_decimal(row["target_qty"], "target_qty"),
        }
        normalized_rows.append(normalized)
    return {
        "schema_version": P2P_GOAL_SCHEMA_VERSION,
        "source_doctype": source_doctype,
        "source_name": source_name,
        "source_rows": normalized_rows,
        "settlement_endpoint": P2P_SETTLEMENT_ENDPOINT,
    }


def _goal_digest(goal: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(goal).encode("utf-8")).hexdigest()


def _stored_p2p_goal(run: Any) -> dict[str, Any]:
    raw = getattr(run, "p2p_goal_json", None)
    state = str(getattr(run, "p2p_goal_state", "MISSING") or "MISSING")
    try:
        version = int(getattr(run, "p2p_goal_version", 0) or 0)
    except TypeError, ValueError:
        version = 0
    digest = str(getattr(run, "p2p_goal_digest", "") or "")
    if not raw or state == "MISSING" or version <= 0:
        return {
            "schema_version": P2P_GOAL_SCHEMA_VERSION,
            "state": "MISSING",
            "version": version,
            "digest": digest or None,
            "goal": None,
        }
    try:
        goal = normalize_p2p_goal(raw)
    except GatewayFault:
        return {
            "schema_version": P2P_GOAL_SCHEMA_VERSION,
            "state": "INVALID",
            "version": version,
            "digest": digest or None,
            "goal": None,
        }
    if str(getattr(run, "p2p_goal_schema_version", "1") or "1") != P2P_GOAL_SCHEMA_VERSION:
        state = "INVALID"
    elif not digest or digest != _goal_digest(goal):
        state = "INVALID"
    return {
        "schema_version": P2P_GOAL_SCHEMA_VERSION,
        "state": state if state in {"CONFIRMED", "STALE", "INVALID"} else "INVALID",
        "version": version,
        "digest": digest or None,
        "goal": goal,
    }


def _source_goal_facts(goal: dict[str, Any], run: Any) -> tuple[dict[str, Any], list[str]]:
    """Read only the current PO facts needed for deterministic completion."""

    reasons: list[str] = []
    source_name = str(goal["source_name"])
    actor = str(getattr(frappe.session, "user", "Guest") or "Guest")
    if not frappe.has_permission("Purchase Order", "read", doc=source_name, user=actor):
        return {}, ["current Purchase Order read permission is unavailable"]
    source = frappe.db.get_value(
        "Purchase Order",
        source_name,
        ["name", "company", "docstatus", "per_received", "per_billed", "status"],
        as_dict=True,
    )
    if not source:
        return {}, ["source Purchase Order is unavailable"]
    if str(source.company) != str(run.company_scope):
        return {}, ["source Purchase Order is outside the Run company scope"]
    row_names = [str(row["source_row"]) for row in goal["source_rows"]]
    rows = frappe.get_all(
        "Purchase Order Item",
        filters={"parent": source_name, "name": ["in", row_names]},
        fields=[
            "name",
            "item_code",
            "qty",
            "uom",
            "warehouse",
            "received_qty",
            "billed_amt",
            "rate",
        ],
        ignore_permissions=True,
        limit_page_length=100,
    )
    by_name = {str(row.name): row for row in rows}
    row_facts: list[dict[str, str]] = []
    for target in goal["source_rows"]:
        row = by_name.get(str(target["source_row"]))
        if row is None:
            reasons.append(f"source row {target['source_row']} is unavailable")
            continue
        if str(row.item_code) != str(target["item_code"]):
            reasons.append(f"source row {target['source_row']} item changed")
            continue
        try:
            ordered = Decimal(str(row.qty or 0))
            target_qty = Decimal(str(target["target_qty"]))
            received = Decimal(str(row.received_qty or 0))
            billed_amount = Decimal(str(row.billed_amt or 0))
            rate = Decimal(str(row.rate or 0))
        except InvalidOperation, TypeError, ValueError:
            reasons.append(f"source row {target['source_row']} has non-numeric ERP facts")
            continue
        if target_qty > ordered:
            reasons.append(f"source row {target['source_row']} target exceeds ordered quantity")
        target_amount = target_qty * rate
        billed_qty = billed_amount / rate if rate > 0 else Decimal("0")
        row_facts.append(
            {
                "source_row": str(row.name),
                "item_code": str(row.item_code),
                "target_qty": format(target_qty.normalize(), "f"),
                "ordered_qty": format(ordered.normalize(), "f"),
                "received_qty": format(received.normalize(), "f"),
                "billed_amount": format(billed_amount.normalize(), "f"),
                "billed_qty": format(billed_qty.normalize(), "f"),
                "target_amount": format(target_amount.normalize(), "f"),
            }
        )
    return {
        "source": {
            "doctype": "Purchase Order",
            "name": source_name,
            "company": str(source.company),
            "docstatus": int(source.docstatus or 0),
            "per_received": str(source.per_received),
            "per_billed": str(source.per_billed),
            "status": str(source.status or ""),
        },
        "rows": row_facts,
    }, reasons


def _linked_invoice_facts(
    source_name: str, row_names: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    reasons: list[str] = []
    target_rows = {str(row_name) for row_name in row_names}
    links: list[Any] = []
    try:
        direct_links = frappe.get_all(
            "Purchase Invoice Item",
            filters={"purchase_order": source_name},
            fields=[
                "name",
                "parent",
                "purchase_order",
                "po_detail",
                "purchase_receipt",
                "pr_detail",
                "qty",
                "amount",
            ],
            ignore_permissions=True,
            limit_page_length=200,
        )
        links.extend(
            row for row in direct_links if str(getattr(row, "po_detail", "") or "") in target_rows
        )
        receipt_rows = frappe.get_all(
            "Purchase Receipt Item",
            filters={"purchase_order": source_name, "purchase_order_item": ["in", row_names]},
            fields=["name", "parent"],
            ignore_permissions=True,
            limit_page_length=200,
        )
        receipt_names = sorted({str(row.parent) for row in receipt_rows if row.parent})
        receipt_item_names = {str(row.name) for row in receipt_rows if getattr(row, "name", None)}
        if receipt_names:
            receipt_links = frappe.get_all(
                "Purchase Invoice Item",
                filters={"purchase_receipt": ["in", receipt_names]},
                fields=[
                    "name",
                    "parent",
                    "purchase_order",
                    "po_detail",
                    "purchase_receipt",
                    "pr_detail",
                    "qty",
                    "amount",
                ],
                ignore_permissions=True,
                limit_page_length=200,
            )
            links.extend(
                row
                for row in receipt_links
                if str(getattr(row, "pr_detail", "") or "") in receipt_item_names
            )
    except Exception:
        return [], ["linked Purchase Invoice facts are unavailable"]
    unique_links = {
        (str(getattr(link, "parent", "")), str(getattr(link, "name", ""))): link
        for link in links
        if getattr(link, "parent", None)
    }
    invoice_names = sorted({parent for parent, _name in unique_links})
    invoices: list[dict[str, Any]] = []
    for name in invoice_names:
        row = frappe.db.get_value(
            "Purchase Invoice",
            name,
            ["name", "docstatus", "status", "grand_total", "outstanding_amount", "currency"],
            as_dict=True,
        )
        if not row or int(row.docstatus or 0) != 1:
            reasons.append(f"linked Purchase Invoice {name} is not submitted")
            continue
        try:
            grand_total = Decimal(str(row.grand_total or 0))
            outstanding = Decimal(str(row.outstanding_amount or 0))
        except InvalidOperation, TypeError, ValueError:
            reasons.append(f"linked Purchase Invoice {name} has non-numeric accounting facts")
            continue
        if outstanding < 0 or outstanding > grand_total:
            reasons.append(f"linked Purchase Invoice {name} outstanding is invalid")
        invoices.append(
            {
                "name": name,
                "status": str(row.status or ""),
                "grand_total": format(grand_total.normalize(), "f"),
                "outstanding_amount": format(outstanding.normalize(), "f"),
                "currency": str(row.currency or ""),
            }
        )
    return invoices, reasons


def p2p_goal_progress(run: Any, entries: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return deterministic target progress and completion conditions."""

    record = _stored_p2p_goal(run)
    base: dict[str, Any] = {
        "goal_state": record["state"],
        "goal_version": record["version"],
        "goal_digest": record["digest"],
        "settlement_endpoint": P2P_SETTLEMENT_ENDPOINT,
        "complete": False,
        "target_qty": "0",
        "received_qty": "0",
        "remaining_qty": "0",
        "target_amount": "0",
        "billed_amount": "0",
        "remaining_amount": "0",
        "outstanding_amount": "0",
        "invoices": [],
        "blocked_reasons": [],
    }
    if record["state"] != "CONFIRMED" or not record["goal"]:
        base["blocked_reasons"] = ["P2P business goal requires initiator confirmation"]
        return base
    facts, reasons = _source_goal_facts(record["goal"], run)
    row_facts = facts.get("rows", []) if facts else []
    target_qty = sum((Decimal(row["target_qty"]) for row in row_facts), Decimal("0"))
    received_qty = sum(
        (min(Decimal(row["received_qty"]), Decimal(row["target_qty"])) for row in row_facts),
        Decimal("0"),
    )
    target_amount = sum((Decimal(row["target_amount"]) for row in row_facts), Decimal("0"))
    billed_amount = sum(
        (min(Decimal(row["billed_amount"]), Decimal(row["target_amount"])) for row in row_facts),
        Decimal("0"),
    )
    row_names = [str(row["source_row"]) for row in record["goal"]["source_rows"]]
    invoices, invoice_reasons = _linked_invoice_facts(record["goal"]["source_name"], row_names)
    outstanding = sum((Decimal(row["outstanding_amount"]) for row in invoices), Decimal("0"))
    reasons.extend(invoice_reasons)
    if not invoices:
        reasons.append("no submitted Purchase Invoice is linked to the target rows")
    base["goal_state"] = "STALE" if reasons else record["state"]
    complete = (
        not reasons
        and len(row_facts) == len(record["goal"]["source_rows"])
        and received_qty >= target_qty
        and billed_amount >= target_amount
        and outstanding == 0
    )
    base.update(
        {
            "source": facts.get("source") if facts else None,
            "rows": row_facts,
            "target_qty": format(target_qty.normalize(), "f"),
            "received_qty": format(received_qty.normalize(), "f"),
            "remaining_qty": format(max(target_qty - received_qty, Decimal("0")).normalize(), "f"),
            "target_amount": format(target_amount.normalize(), "f"),
            "billed_amount": format(billed_amount.normalize(), "f"),
            "remaining_amount": format(
                max(target_amount - billed_amount, Decimal("0")).normalize(), "f"
            ),
            "outstanding_amount": format(outstanding.normalize(), "f"),
            "invoices": invoices,
            "complete": complete,
            "blocked_reasons": sorted(set(reasons)),
        }
    )
    return base


def get_p2p_goal(run_id: str) -> dict[str, Any]:
    if not frappe.db.exists("Synora Agent Run", run_id):
        raise GatewayFault("RUN_REJECTED", "run is not available", 404)
    run = frappe.get_doc("Synora Agent Run", run_id)
    record = _stored_p2p_goal(run)
    return {
        "schema_version": P2P_GOAL_SCHEMA_VERSION,
        "state": record["state"],
        "version": record["version"],
        "digest": record["digest"],
        "goal": record["goal"],
    }


def _invalidate_pending_p2p_actions(run_id: str, reason: str, correlation_id: str) -> int:
    rows = frappe.get_all(
        "Synora Proposed Action",
        filters={"run": run_id, "action_type": ["in", sorted(P2P_ACTION_TYPES)]},
        fields=["name", "state", "state_version", "proposal_digest"],
        order_by="creation asc",
        limit_page_length=200,
        ignore_permissions=True,
    )
    changed = 0
    for row in rows:
        state = str(row.state)
        if state not in {"DRAFT", "AWAITING_APPROVAL", "APPROVED"}:
            continue
        target = "INVALID" if state == "DRAFT" else "EXPIRED"
        transition_action_state(
            str(row.name),
            target,
            expected_version=int(row.state_version),
            reason=reason[:2_000],
            correlation_id=correlation_id,
            approval_digest=str(row.proposal_digest),
        )
        changed += 1
    return changed


def confirm_p2p_goal(run_id: str, goal: object, correlation_id: str) -> dict[str, Any]:
    """Persist an initiator-confirmed goal and invalidate stale candidates."""

    run = _authorized_run(run_id, lock=True)
    if str(run.status) != "ACTIVE" or bool(run.revoked):
        raise GatewayFault("CONFLICT", "P2P Run is no longer active", 409)
    if str(run.run_state) in {"SUCCEEDED", "CANCELLED", "EXPIRED", "FAILED"}:
        raise GatewayFault("CONFLICT", "P2P Run is already terminal", 409)
    normalized = normalize_p2p_goal(goal)
    facts, reasons = _source_goal_facts(normalized, run)
    if reasons or not facts:
        raise GatewayFault(
            "CONFLICT",
            "; ".join(reasons[:3]) or "P2P goal source is unavailable",
            409,
        )
    for row in normalized["source_rows"]:
        fact = next(
            (item for item in facts["rows"] if item["source_row"] == row["source_row"]),
            None,
        )
        if fact is None:
            raise GatewayFault("CONFLICT", f"source row {row['source_row']} is unavailable", 409)
        if Decimal(row["target_qty"]) > Decimal(fact["ordered_qty"]):
            raise GatewayFault(
                "CONFLICT",
                f"source row {row['source_row']} target exceeds ordered quantity",
                409,
            )
    next_digest = _goal_digest(normalized)
    previous_state = str(getattr(run, "p2p_goal_state", "MISSING") or "MISSING")
    previous_digest = str(getattr(run, "p2p_goal_digest", "") or "")
    if previous_state == "CONFIRMED" and previous_digest == next_digest:
        return {
            "run_id": run_id,
            "goal": get_p2p_goal(run_id),
            "invalidated_actions": 0,
        }
    try:
        current_version = int(getattr(run, "p2p_goal_version", 0) or 0)
    except TypeError, ValueError:
        current_version = 0
    next_version = current_version + 1
    run.p2p_goal_schema_version = P2P_GOAL_SCHEMA_VERSION
    run.p2p_goal_json = _canonical_json(normalized)
    run.p2p_goal_version = next_version
    run.p2p_goal_digest = next_digest
    run.p2p_goal_state = "CONFIRMED"
    run.p2p_goal_confirmed_by = str(frappe.session.user)
    run.p2p_goal_confirmed_at = now_datetime()
    run.flags.synora_p2p_goal_update = True
    run.save(ignore_permissions=True)
    invalidated_actions = 0
    if previous_digest and previous_digest != run.p2p_goal_digest:
        invalidated_actions = _invalidate_pending_p2p_actions(
            run_id,
            f"P2P goal version changed to {next_version}; candidate requires regeneration",
            correlation_id,
        )
    frappe.db.commit()
    return {
        "run_id": run_id,
        "goal": get_p2p_goal(run_id),
        "invalidated_actions": invalidated_actions,
    }


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _stable_refs(value: Iterable[tuple[str, str]] | None) -> list[list[str]]:
    """Turn internal reference tuples/sets into a deterministic JSON value."""

    return [list(ref) for ref in sorted(value or set())]


def _action_from_doc(doc: Any) -> Any:
    try:
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
    except Exception as error:
        raise GatewayFault("CONFLICT", "governed action record is invalid", 409) from error


def _target_ref(
    action: Any, receipt: Any | None = None, reservation: dict[str, Any] | None = None
) -> tuple[str, str] | None:
    if (
        receipt is not None
        and getattr(receipt, "target_doctype", None)
        and getattr(receipt, "target_name", None)
    ):
        return str(receipt.target_doctype), str(receipt.target_name)
    if reservation and reservation.get("target_doctype") and reservation.get("target_name"):
        return str(reservation["target_doctype"]), str(reservation["target_name"])
    payload = action.payload
    source_doctype = str(payload.get("source_doctype") or "")
    source_name = str(payload.get("source_name") or "")
    return (source_doctype, source_name) if source_doctype and source_name else None


def _related_refs(action: Any, receipt: Any | None = None) -> set[tuple[str, str]]:
    refs: set[tuple[str, str]] = set()
    payload = action.payload
    source = _target_ref(action, receipt)
    if source:
        refs.add(source)
    source_doctype = str(payload.get("source_doctype") or "")
    source_name = str(payload.get("source_name") or "")
    if source_doctype and source_name:
        refs.add((source_doctype, source_name))
    for reference in payload.get("references", []) or []:
        if isinstance(reference, dict):
            doctype = str(reference.get("reference_doctype") or "")
            name = str(reference.get("reference_name") or "")
            if doctype and name:
                refs.add((doctype, name))
    if receipt is not None:
        try:
            verified = json.loads(receipt.verified_fields_json or "{}")
        except (TypeError, ValueError):  # fmt: skip
            verified = {}
        if isinstance(verified, dict):
            for key, value in verified.items():
                if (
                    key.endswith((".purchase_order", ".purchase_receipt", ".reference_name"))
                    and value
                ):
                    if key.endswith("purchase_order"):
                        doctype = "Purchase Order"
                    elif key.endswith("purchase_receipt"):
                        doctype = "Purchase Receipt"
                    else:
                        doctype = "Purchase Invoice"
                    refs.add((doctype, str(value)))
    return refs


def _minimal_entry(
    doc: Any, receipt: Any | None = None, reservation: dict[str, Any] | None = None
) -> dict[str, Any]:
    action = _action_from_doc(doc)
    return {
        "action": action,
        "action_id": str(action.action_id),
        "action_type": str(action.action_type),
        "run_id": str(action.run_id),
        "state": str(doc.state),
        "state_reason": str(doc.state_reason or ""),
        "receipt": receipt,
        "target_ref": _target_ref(action, receipt, reservation),
        "related_refs": _related_refs(action, receipt),
        "reservation": reservation,
        "created_at": str(getattr(doc, "creation", "") or ""),
    }


def _entries_for_run(run_id: str) -> list[dict[str, Any]]:
    rows = frappe.get_all(
        "Synora Proposed Action",
        filters={"run": run_id, "action_type": ["in", sorted(P2P_ACTION_TYPES)]},
        fields=["name"],
        order_by="creation asc, name asc",
        limit_page_length=200,
        ignore_permissions=True,
    )
    entries: list[dict[str, Any]] = []
    for row in rows:
        receipt_rows = frappe.get_all(
            "Synora Execution Receipt",
            filters={"action": row.name},
            fields=["name"],
            order_by="completed_at desc, creation desc",
            limit_page_length=1,
            ignore_permissions=True,
        )
        receipt = (
            frappe.get_doc("Synora Execution Receipt", receipt_rows[0].name)
            if receipt_rows
            else None
        )
        reservation_rows = frappe.get_all(
            "Synora Execution Reservation",
            filters={"action": row.name},
            fields=[
                "reservation_id",
                "status",
                "target_doctype",
                "target_name",
                "executor",
                "receipt",
                "response_category",
                "failure_category",
                "lease_expires_at",
            ],
            order_by="creation desc",
            limit_page_length=1,
            ignore_permissions=True,
        )
        reservation = reservation_rows[0] if reservation_rows else None
        entries.append(
            _minimal_entry(frappe.get_doc("Synora Proposed Action", row.name), receipt, reservation)
        )
    return entries


def infer_dependencies(entries: Iterable[dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    """Infer only same-Run, same-document edges from persisted facts.

    This is deliberately bounded O(n²); a Run is capped at 200 actions and a
    database-backed PlanStep keeps the graph stable across process restarts.
    """

    ordered = sorted(
        (entry for entry in entries if entry.get("action_type") in ACTION_ORDER),
        key=lambda entry: (
            str(entry.get("created_at") or ""),
            str(entry.get("action_id") or ""),
        ),
    )
    result: dict[str, tuple[str, ...]] = {}
    for index, current in enumerate(ordered):
        rank = ACTION_ORDER[str(current["action_type"])]
        current_refs = set(current.get("related_refs") or ())
        candidates: list[tuple[str, int, str]] = []
        for prior in ordered[:index]:
            prior_rank = ACTION_ORDER.get(str(prior["action_type"]))
            if prior_rank is None or prior_rank >= rank:
                continue
            prior_refs = set(prior.get("related_refs") or ())
            if current_refs and current_refs.intersection(prior_refs):
                candidates.append(
                    (
                        str(prior["action_id"]),
                        prior_rank,
                        str(prior.get("created_at") or ""),
                    )
                )
        # Keep every direct stage edge when documents share a source; this
        # preserves parallel partial actions while preventing a skipped write.
        result[str(current["action_id"])] = tuple(
            action_id
            for action_id, _rank, _created in sorted(
                candidates,
                key=lambda value: (value[1], value[2], value[0]),
            )
        )
    return result


def _receipt_state(entry: dict[str, Any]) -> str | None:
    receipt = entry.get("receipt")
    if receipt is None:
        return None
    return str(getattr(receipt, "final_state", "") or "")


def derive_step_views(entries: Iterable[dict[str, Any]]) -> tuple[P2PPlanStepView, ...]:
    """Project Action/Approval/Reservation/Receipt facts into PlanStep views."""

    ordered = sorted(
        entries,
        key=lambda entry: (
            str(entry.get("created_at") or ""),
            str(entry.get("action_id") or ""),
        ),
    )
    dependencies = infer_dependencies(ordered)
    raw: dict[str, str] = {}
    for entry in ordered:
        action_id = str(entry["action_id"])
        state = str(entry.get("state") or "DRAFT")
        receipt_state = _receipt_state(entry)
        reservation = entry.get("reservation") or {}
        # An EXECUTED Action without a durable success Receipt is ambiguous;
        # it must remain recoverable and cannot make the Run appear complete.
        if receipt_state in SUCCESS_RECEIPT_STATES:
            projected = "SUCCEEDED"
        elif (
            receipt_state in FAILED_RECEIPT_STATES
            or str(reservation.get("status") or "") in FAILED_RECEIPT_STATES
            or state == "POLICY_REJECTED"
        ):
            projected = "FAILED"
        elif (
            receipt_state in UNCERTAIN_RECEIPT_STATES
            or str(reservation.get("status") or "") in UNCERTAIN_RECEIPT_STATES
        ):
            projected = "RECONCILIATION_REQUIRED"
        elif str(reservation.get("status") or "") == "STARTED":
            projected = "EXECUTING"
        elif state == "EXECUTED":
            projected = "RECONCILIATION_REQUIRED"
        elif state == "AWAITING_APPROVAL":
            projected = "WAITING_APPROVAL"
        elif state == "APPROVED":
            projected = "READY"
        elif state in {"DECLINED", "EXPIRED"}:
            projected = state
        else:
            projected = "PLANNED"
        raw[action_id] = projected

    views: list[P2PPlanStepView] = []
    for order, entry in enumerate(ordered, 1):
        action_id = str(entry["action_id"])
        deps = dependencies.get(action_id, ())
        projected = raw[action_id]
        blocked_reason: str | None = None
        if projected not in TERMINAL_STEP_STATES:
            dependency_states = [(dep, raw.get(dep, "BLOCKED")) for dep in deps]
            failed = next(
                (
                    item
                    for item in dependency_states
                    if item[1]
                    in {
                        "FAILED",
                        "RECONCILIATION_REQUIRED",
                        "REINVESTIGATION_REQUIRED",
                        "CANCELLED",
                        "EXPIRED",
                    }
                ),
                None,
            )
            pending = next(
                (item for item in dependency_states if item[1] != "SUCCEEDED"),
                None,
            )
            if failed:
                projected = "BLOCKED"
                blocked_reason = f"dependency {failed[0]} is {failed[1]}"
            elif pending:
                projected = "WAITING_DEPENDENCY"
                blocked_reason = f"waiting for dependency {pending[0]}"
        views.append(
            P2PPlanStepView(
                action_id=action_id,
                step_id=f"p2p-{action_id[:12]}",
                order=order,
                state=projected,
                depends_on=deps,
                blocked_reason=blocked_reason,
                reinvestigation_required=bool(entry.get("reinvestigation_required", False)),
            )
        )
    return tuple(views)


def _overall_status(
    run_state: str,
    views: tuple[P2PPlanStepView, ...],
    *,
    goal_state: str = "MISSING",
    business_complete: bool = False,
) -> str:
    if run_state in {"CANCELLED", "EXPIRED"}:
        return run_state
    if run_state in {"FAILED", "SUCCEEDED"} and not views:
        return run_state
    if any(
        view.state == "REINVESTIGATION_REQUIRED" or view.reinvestigation_required for view in views
    ):
        return "REINVESTIGATION_REQUIRED"
    if any(view.state == "RECONCILIATION_REQUIRED" for view in views):
        return "RECONCILIATION_REQUIRED"
    if any(view.state == "BLOCKED" for view in views):
        return "BLOCKED"
    if any(view.state == "FAILED" for view in views):
        return "FAILED"
    if views and all(view.state == "SUCCEEDED" for view in views):
        if goal_state == "MISSING":
            return "WAITING_GOAL_CONFIRMATION"
        if goal_state != "CONFIRMED":
            return "REINVESTIGATION_REQUIRED"
        if not business_complete:
            return "WAITING_BUSINESS_FACTS"
        return "SUCCEEDED"
    if any(view.state == "WAITING_APPROVAL" for view in views):
        return "WAITING_APPROVAL"
    if any(view.state == "WAITING_DEPENDENCY" for view in views):
        return "WAITING_DEPENDENCY"
    if any(view.state == "EXECUTING" for view in views):
        return "EXECUTING"
    if not views:
        if goal_state == "MISSING":
            return "WAITING_GOAL_CONFIRMATION"
        if goal_state != "CONFIRMED":
            return "REINVESTIGATION_REQUIRED"
        if not business_complete:
            return "WAITING_BUSINESS_FACTS"
    return "PLANNED" if not views else "IN_PROGRESS"


def chain_from_entries(
    run_state: str,
    entries: Iterable[dict[str, Any]],
    *,
    goal: dict[str, Any] | None = None,
    business_progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    views = derive_step_views(entries)
    entries_by_id = {str(entry["action_id"]): entry for entry in entries}
    timeline: list[dict[str, Any]] = []
    for view in views:
        entry = entries_by_id[view.action_id]
        action = entry["action"]
        timeline.append(
            {
                "step_id": view.step_id,
                "action_id": view.action_id,
                "action_type": str(action.action_type),
                "state": view.state,
                "target_doctype": TARGET_DOCTYPES.get(str(action.action_type)),
                "target_name": (entry.get("target_ref") or (None, None))[1],
                "depends_on": list(view.depends_on),
                "blocked_reason": view.blocked_reason,
                "receipt_state": _receipt_state(entry),
            }
        )
    progress = business_progress or {
        "goal_state": "CONFIRMED" if goal else "MISSING",
        "goal_version": 1 if goal else 0,
        "goal_digest": None,
        "settlement_endpoint": P2P_SETTLEMENT_ENDPOINT,
        "complete": bool(goal),
        "blocked_reasons": [] if goal else ["P2P business goal requires initiator confirmation"],
    }
    goal_state = str(progress.get("goal_state") or "MISSING")
    business_complete = bool(progress.get("complete"))
    status = _overall_status(
        run_state,
        views,
        goal_state=goal_state,
        business_complete=business_complete,
    )
    blocked_reasons = [view.blocked_reason for view in views if view.blocked_reason]
    blocked_reasons.extend(str(reason) for reason in progress.get("blocked_reasons", []) if reason)
    steps_complete = bool(views) and all(view.state == "SUCCEEDED" for view in views)
    completion_ready = steps_complete and goal_state == "CONFIRMED" and business_complete
    next_step = next(
        (
            view.step_id
            for view in views
            if view.state in {"WAITING_APPROVAL", "READY", "EXECUTING", "WAITING_DEPENDENCY"}
        ),
        None,
    )
    return {
        "schema_version": P2P_STEP_SCHEMA_VERSION,
        "status": status,
        "completion_ready": completion_ready,
        "steps_complete": steps_complete,
        "business_goal_complete": business_complete,
        "goal": {
            "schema_version": P2P_GOAL_SCHEMA_VERSION,
            "state": goal_state,
            "version": int(progress.get("goal_version") or 0),
            "digest": progress.get("goal_digest"),
            "target": goal,
        },
        "business_progress": progress,
        "needs_reinvestigation": status == "REINVESTIGATION_REQUIRED",
        "next_step_id": next_step,
        "blocked_reasons": blocked_reasons,
        "steps": [
            {
                "step_id": view.step_id,
                "action_id": view.action_id,
                "order": view.order,
                "state": view.state,
                "depends_on": list(view.depends_on),
                "blocked_reason": view.blocked_reason,
                "reinvestigation_required": view.reinvestigation_required,
            }
            for view in views
        ],
        "timeline": timeline,
    }


def _ensure_step_doc(entry: dict[str, Any], view: P2PPlanStepView) -> Any:
    action = entry["action"]
    existing = frappe.db.get_value("Synora P2P Plan Step", {"action": view.action_id}, "name")
    if existing:
        return frappe.get_doc("Synora P2P Plan Step", existing)
    source = action.payload
    doc = frappe.get_doc(
        {
            "doctype": "Synora P2P Plan Step",
            "run": action.run_id,
            "action": view.action_id,
            "step_id": view.step_id,
            "step_order": view.order,
            "depends_on_json": _canonical_json(list(view.depends_on)),
            "state": view.state,
            "target_doctype": TARGET_DOCTYPES.get(action.action_type),
            "target_name": (entry.get("target_ref") or (None, None))[1],
            "source_doctype": source.get("source_doctype"),
            "source_name": source.get("source_name"),
            "blocked_reason": view.blocked_reason,
            "reinvestigation_required": int(view.reinvestigation_required),
            "observed_digest": hashlib.sha256(
                _canonical_json(_stable_refs(entry.get("related_refs"))).encode()
            ).hexdigest(),
            "correlation_id": action.correlation_id,
            "last_evaluated_at": now_datetime(),
        }
    )
    doc.flags[P2P_STEP_SERVICE_FLAG] = True
    doc.insert(ignore_permissions=True)
    return doc


def sync_p2p_plan_steps(run_id: str) -> dict[str, Any]:
    entries = _entries_for_run(run_id)
    run = frappe.get_doc("Synora Agent Run", run_id)
    goal_record = _stored_p2p_goal(run)
    chain = chain_from_entries(
        str(run.run_state or ""),
        entries,
        goal=goal_record["goal"],
        business_progress=p2p_goal_progress(run, entries),
    )
    views = derive_step_views(entries)
    for entry, view in zip(
        sorted(
            entries,
            key=lambda value: (
                str(value.get("created_at") or ""),
                str(value.get("action_id") or ""),
            ),
        ),
        views,
        strict=True,
    ):
        doc = _ensure_step_doc(entry, view)
        doc.depends_on_json = _canonical_json(list(view.depends_on))
        doc.state = view.state
        doc.target_doctype = TARGET_DOCTYPES.get(entry["action_type"])
        doc.target_name = (entry.get("target_ref") or (None, None))[1]
        doc.source_doctype = entry["action"].payload.get("source_doctype")
        doc.source_name = entry["action"].payload.get("source_name")
        doc.blocked_reason = view.blocked_reason
        doc.reinvestigation_required = int(view.reinvestigation_required)
        doc.observed_digest = hashlib.sha256(
            _canonical_json(_stable_refs(entry.get("related_refs"))).encode()
        ).hexdigest()
        doc.last_evaluated_at = now_datetime()
        doc.flags[P2P_STEP_SERVICE_FLAG] = True
        doc.save(ignore_permissions=True)
    return chain


def ensure_p2p_plan_step(action: Any) -> dict[str, Any]:
    sync_p2p_plan_steps(str(action.run_id))
    return get_p2p_chain(str(action.run_id))


def get_p2p_chain(
    run_id: str, *, entries: Iterable[dict[str, Any]] | None = None, run_state: str | None = None
) -> dict[str, Any]:
    resolved_entries = list(entries) if entries is not None else _entries_for_run(run_id)
    run = None
    try:
        run = frappe.get_doc("Synora Agent Run", run_id)
    except Exception:
        run = None
    state = run_state or str(getattr(run, "run_state", "") or "")
    goal_record = _stored_p2p_goal(run) if run is not None else {"goal": None}
    progress = (
        p2p_goal_progress(run, resolved_entries)
        if run is not None
        else {
            "goal_state": "CONFIRMED" if goal_record["goal"] else "MISSING",
            "goal_version": 1 if goal_record["goal"] else 0,
            "goal_digest": None,
            "settlement_endpoint": P2P_SETTLEMENT_ENDPOINT,
            "complete": False,
            "blocked_reasons": (
                [] if goal_record["goal"] else ["P2P business goal requires initiator confirmation"]
            ),
        }
    )
    chain = chain_from_entries(
        state,
        resolved_entries,
        goal=goal_record["goal"],
        business_progress=progress,
    )
    stored = frappe.get_all(
        "Synora P2P Plan Step",
        filters={"run": run_id},
        fields=["action", "state", "depends_on_json", "blocked_reason", "reinvestigation_required"],
        order_by="step_order asc",
        limit_page_length=200,
        ignore_permissions=True,
    )
    if stored:
        by_action = {str(row.action): row for row in stored}
        for step in chain["steps"]:
            row = by_action.get(str(step["action_id"]))
            if row:
                try:
                    dependencies = json.loads(row.depends_on_json or "[]")
                except (TypeError, ValueError):  # fmt: skip
                    dependencies = step["depends_on"]
                if isinstance(dependencies, list):
                    step["depends_on"] = dependencies
                step["stored_state"] = str(row.state)
                step["reinvestigation_required"] = bool(row.reinvestigation_required)
                if row.blocked_reason:
                    step["blocked_reason"] = str(row.blocked_reason)
    return chain


def assert_p2p_action_dependencies(action_id: str) -> None:
    entries = _entries_for_run(
        str(frappe.db.get_value("Synora Proposed Action", action_id, "run") or "")
    )
    current = next((entry for entry in entries if entry["action_id"] == action_id), None)
    if current is None:
        raise GatewayFault("NOT_FOUND", "governed action is not available", 404)
    dependencies = infer_dependencies(entries).get(action_id, ())
    by_id = {entry["action_id"]: entry for entry in entries}
    for dependency_id in dependencies:
        dependency = by_id.get(dependency_id)
        if dependency is None:
            raise GatewayFault("CONFLICT", "P2P action dependency is unavailable", 409)
        receipt_state = _receipt_state(dependency)
        if (
            str(dependency.get("state")) != "EXECUTED"
            or receipt_state not in SUCCESS_RECEIPT_STATES
        ):
            reason = str(dependency.get("state_reason") or receipt_state or "not confirmed")
            raise GatewayFault(
                "CONFLICT",
                f"P2P action dependency is not confirmed: {dependency_id} ({reason})",
                409,
            )


def live_reinvestigation(run_id: str, actor: str) -> dict[str, Any]:
    """Read current target facts and flag drift without invoking any writer."""

    entries = _entries_for_run(run_id)
    drift: list[dict[str, str]] = []
    for entry in entries:
        receipt = entry.get("receipt")
        if (
            receipt is None
            or str(getattr(receipt, "final_state", "")) not in SUCCESS_RECEIPT_STATES
        ):
            continue
        target_name = str(getattr(receipt, "target_name", "") or "")
        target_doctype = str(getattr(receipt, "target_doctype", "") or "")
        if not target_name or not target_doctype:
            continue
        if not frappe.has_permission(target_doctype, "read", user=actor):
            drift.append(
                {
                    "action_id": entry["action_id"],
                    "reason": "current target read permission is unavailable",
                }
            )
            continue
        try:
            target = frappe.get_doc(target_doctype, target_name)
            from synora_agentic_erp.governance.execution_contracts import (
                p2p_receipt_evidence_matches,
            )
            from synora_agentic_erp.governance.p2p_execution import p2p_read_back_with_financials

            current = p2p_read_back_with_financials(entry["action"], target)
            recorded = json.loads(receipt.verified_fields_json or "{}")
            if not p2p_receipt_evidence_matches(entry["action"], recorded, current):
                drift.append(
                    {
                        "action_id": entry["action_id"],
                        "reason": "ERP target no longer matches the saved Receipt",
                    }
                )
        except Exception as error:
            drift.append({"action_id": entry["action_id"], "reason": str(error)[:240]})
    return {"drift": drift, "needs_reinvestigation": bool(drift)}


def _authorized_run(run_id: str, *, lock: bool = False) -> Any:
    if not frappe.db.exists("Synora Agent Run", run_id):
        raise GatewayFault("RUN_REJECTED", "run is not available", 404)
    if lock:
        locked = frappe.db.sql(
            """
            SELECT name
            FROM `tabSynora Agent Run`
            WHERE name = %s
            FOR UPDATE
            """,
            (run_id,),
            as_dict=True,
        )
        if not locked:
            raise GatewayFault("RUN_REJECTED", "run is not available", 404)
    run = frappe.get_doc("Synora Agent Run", run_id)
    actor = str(getattr(frappe.session, "user", "Guest") or "Guest")
    if actor == "Guest" or (
        actor != str(run.initiator) and "System Manager" not in frappe.get_roles(actor)
    ):
        raise GatewayFault("RUN_REJECTED", "run is not available", 404)
    return run


def _run_result(run: Any, chain: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(run.name),
        "run_state": str(run.run_state),
        "status": str(run.status),
        "state_version": int(run.state_version),
        "chain": chain,
    }


def _active_reservations(run_id: str) -> list[dict[str, Any]]:
    return frappe.get_all(
        "Synora Execution Reservation",
        filters={"run": run_id, "status": ["in", ["STARTED", "RECONCILIATION_REQUIRED"]]},
        fields=["reservation_id", "action", "status"],
        limit_page_length=20,
        ignore_permissions=True,
    )


def _expire_if_needed(run: Any, correlation_id: str) -> bool:
    """Close an expired PLAN_EXECUTE Run only when no side effect is active."""

    if str(run.status) != "ACTIVE" or bool(run.revoked) or not workflow_expired(run):
        return False
    active = _active_reservations(str(run.name))
    if active:
        raise GatewayFault(
            "UNCERTAIN_RESULT",
            "P2P Run deadline passed while a side effect still needs reconciliation",
            503,
        )
    if str(run.run_state) not in {
        "CREATED",
        "ANALYZING",
        "PROPOSED",
        "AWAITING_APPROVAL",
        "EXECUTING",
        "RECONCILIATION_REQUIRED",
    }:
        return False
    _mark_terminal(run, "EXPIRED", correlation_id)
    frappe.db.commit()
    return True


def _mark_terminal(run: Any, target: str, correlation_id: str) -> None:
    if str(run.run_state) != target:
        validate_transition(str(run.run_state), target)
        _set_run_state(run, target)
    run.flags.synora_revocation = True
    run.revoked = 1
    run.status = "EXPIRED" if target == "EXPIRED" else "REVOKED"
    run.revoked_at = now_datetime()
    run.revoked_by = frappe.session.user
    run.revocation_correlation_id = correlation_id
    run.save(ignore_permissions=True)


def finalize_p2p_run(run_id: str, correlation_id: str) -> dict[str, Any]:
    """Close a P2P Run only after the target and every ERP fact are verified."""

    run = _authorized_run(run_id, lock=True)
    if str(run.run_state) == "SUCCEEDED":
        return _run_result(run, get_p2p_chain(run_id, run_state=str(run.run_state)))
    if str(run.status) != "ACTIVE" or bool(run.revoked):
        raise GatewayFault("CONFLICT", "P2P Run is no longer active", 409)
    active = _active_reservations(run_id)
    if active:
        raise GatewayFault(
            "UNCERTAIN_RESULT",
            "P2P Run has an active or uncertain side effect; reconcile before closing",
            503,
        )
    entries = _entries_for_run(run_id)
    goal_record = _stored_p2p_goal(run)
    progress = p2p_goal_progress(run, entries)
    chain = chain_from_entries(
        str(run.run_state),
        entries,
        goal=goal_record["goal"],
        business_progress=progress,
    )
    if not chain["completion_ready"]:
        reason = "; ".join(chain["blocked_reasons"][:3]) or str(chain["status"])
        raise GatewayFault("CONFLICT", f"P2P Run is not ready to close: {reason}", 409)
    if str(run.run_state) not in {"EXECUTING", "RECONCILIATION_REQUIRED"}:
        raise GatewayFault("CONFLICT", "P2P Run is not in an executable lifecycle state", 409)
    _mark_terminal(run, "SUCCEEDED", correlation_id)
    frappe.db.commit()
    return _run_result(
        run,
        chain_from_entries(
            "SUCCEEDED",
            entries,
            goal=goal_record["goal"],
            business_progress=progress,
        ),
    )


def cancel_p2p_run(run_id: str, correlation_id: str) -> dict[str, Any]:
    """Stop future P2P scheduling while leaving completed ERP business facts intact."""

    run = _authorized_run(run_id, lock=True)
    if str(run.run_state) == "CANCELLED":
        return _run_result(run, get_p2p_chain(run_id, run_state="CANCELLED"))
    if str(run.status) != "ACTIVE" or bool(run.revoked):
        raise GatewayFault("CONFLICT", "P2P Run is no longer active", 409)
    active = _active_reservations(run_id)
    if active:
        raise GatewayFault(
            "UNCERTAIN_RESULT",
            "P2P Run has an active or uncertain side effect; reconcile before cancelling",
            503,
        )
    if str(run.run_state) not in {
        "CREATED",
        "ANALYZING",
        "PROPOSED",
        "AWAITING_APPROVAL",
        "EXECUTING",
        "RECONCILIATION_REQUIRED",
    }:
        raise GatewayFault("CONFLICT", "P2P Run cannot be cancelled in its current state", 409)
    if _expire_if_needed(run, correlation_id):
        return _run_result(run, get_p2p_chain(run_id, run_state="EXPIRED"))
    _mark_terminal(run, "CANCELLED", correlation_id)
    frappe.db.commit()
    return _run_result(run, get_p2p_chain(run_id, run_state="CANCELLED"))


def resume_p2p_run(run_id: str, correlation_id: str) -> dict[str, Any]:
    """Re-read current ERP targets and surface drift without executing a writer."""

    run = _authorized_run(run_id, lock=True)
    if str(run.run_state) in {"CANCELLED", "EXPIRED", "FAILED", "SUCCEEDED"}:
        return _run_result(run, get_p2p_chain(run_id, run_state=str(run.run_state)))
    if _expire_if_needed(run, correlation_id):
        return _run_result(run, get_p2p_chain(run_id, run_state="EXPIRED"))
    # Rebuild the projection before annotating drift so a restarted process
    # can recover even when the previous request stopped before persistence.
    sync_p2p_plan_steps(run_id)
    drift = live_reinvestigation(run_id, str(frappe.session.user))
    chain = get_p2p_chain(run_id, run_state=str(run.run_state))
    if drift["needs_reinvestigation"]:
        for item in drift["drift"]:
            step_name = frappe.db.get_value(
                "Synora P2P Plan Step", {"action": item["action_id"]}, "name"
            )
            if not step_name:
                continue
            step = frappe.get_doc("Synora P2P Plan Step", step_name)
            step.state = "REINVESTIGATION_REQUIRED"
            step.reinvestigation_required = 1
            step.blocked_reason = item["reason"][:2_000]
            step.last_evaluated_at = now_datetime()
            step.flags[P2P_STEP_SERVICE_FLAG] = True
            step.save(ignore_permissions=True)
        if str(run.run_state) == "EXECUTING":
            _set_run_state(run, "RECONCILIATION_REQUIRED")
            run = frappe.get_doc("Synora Agent Run", run_id)
        chain["status"] = "REINVESTIGATION_REQUIRED"
        chain["needs_reinvestigation"] = True
        chain["reinvestigation"] = drift
        frappe.db.commit()
    else:
        chain["reinvestigation"] = drift
    return _run_result(run, chain)


__all__ = [
    "P2P_STEP_SCHEMA_VERSION",
    "assert_p2p_action_dependencies",
    "chain_from_entries",
    "derive_step_views",
    "ensure_p2p_plan_step",
    "get_p2p_chain",
    "infer_dependencies",
    "live_reinvestigation",
    "sync_p2p_plan_steps",
]
