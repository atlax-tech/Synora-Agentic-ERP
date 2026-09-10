"""Pure contracts for the first governed Material Request write."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.governance.contracts import TARGET_DOCTYPES, ProposedAction


class ReadBackMismatch(ValueError):
    """The ERP document does not match the approved critical fields."""


_PI_MUTABLE_RECEIPT_FIELDS = frozenset(
    {
        "status",
        "outstanding_amount",
    }
)


@dataclass(frozen=True)
class ExecutionKey:
    """The immutable tuple that identifies one logical governed write."""

    action_type: str
    target_doctype: str
    company: str
    warehouse: str
    proposal_digest: str
    idempotency_key: str

    def as_tuple(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.action_type,
            self.target_doctype,
            self.company,
            self.warehouse,
            self.proposal_digest,
            self.idempotency_key,
        )


@dataclass(frozen=True)
class ReconciliationClassification:
    """Read-only reconciliation outcome; none of the outcomes permits retry."""

    result_status: str
    reason: str
    can_retry: bool = False

    def __post_init__(self) -> None:
        if self.result_status not in {
            "RECONCILIATION_REQUIRED",
            "RECONCILED_SUCCESS",
            "RECONCILED_FAILURE",
            "MANUAL_INTERVENTION",
        }:
            raise GatewayFault("INVALID_INPUT", "reconciliation status is invalid")
        if not self.reason or len(self.reason) > 2_000 or self.can_retry:
            raise GatewayFault("INVALID_INPUT", "reconciliation result is invalid")


def classify_reconciliation(
    *,
    candidate_count: int,
    matching_count: int,
    lease_expired: bool,
    failure_evidence_complete: bool,
) -> ReconciliationClassification:
    """Classify ERP candidates without ever authorizing another write."""

    if candidate_count < 0 or matching_count < 0 or matching_count > candidate_count:
        raise GatewayFault("INVALID_INPUT", "reconciliation candidate counts are invalid")
    if candidate_count == 1 and matching_count == 1:
        return ReconciliationClassification("RECONCILED_SUCCESS", "one complete ERP match")
    if candidate_count > 0:
        return ReconciliationClassification(
            "MANUAL_INTERVENTION",
            "candidate count or critical fields are ambiguous",
        )
    if not lease_expired:
        return ReconciliationClassification(
            "RECONCILIATION_REQUIRED",
            "execution lease is still active",
        )
    if failure_evidence_complete:
        return ReconciliationClassification(
            "RECONCILED_FAILURE",
            "lease ended with no matching ERP document and complete failure evidence",
        )
    return ReconciliationClassification(
        "MANUAL_INTERVENTION",
        "no ERP document but failure evidence is incomplete",
    )


def material_request_values(action: ProposedAction) -> dict[str, Any]:
    """Build the only allowed MR input from an immutable typed action."""

    if action.action_type != "CREATE_MR_DRAFT":
        raise GatewayFault("INVALID_INPUT", "only CREATE_MR_DRAFT is supported", 400)
    payload = action.payload
    values: dict[str, Any] = {
        "doctype": "Material Request",
        "naming_series": "MAT-MR-.YYYY.-",
        "material_request_type": payload["material_request_type"],
        "company": payload["company"],
        "transaction_date": payload["transaction_date"],
        "items": [],
    }
    items: list[dict[str, Any]] = []
    for raw_item in payload["items"]:
        item: dict[str, Any] = {
            "item_code": raw_item["item_code"],
            "qty": raw_item["qty"],
            "warehouse": raw_item["warehouse"],
            "schedule_date": raw_item["schedule_date"],
        }
        for optional in ("uom", "description"):
            if optional in raw_item:
                item[optional] = raw_item[optional]
        items.append(item)
    values["items"] = items
    return values


def purchase_order_values(action: ProposedAction) -> dict[str, Any]:
    """Build the only allowed PO input from an immutable typed action.

    Phase 6 accepts only each Item's stock UOM, so the fixed conversion factor
    of one is an explicit policy invariant rather than an assumed conversion.
    """

    if action.action_type != "CREATE_PO_DRAFT":
        raise GatewayFault("INVALID_INPUT", "only CREATE_PO_DRAFT is supported", 400)
    payload = action.payload
    values: dict[str, Any] = {
        "doctype": "Purchase Order",
        "naming_series": "PUR-ORD-.YYYY.-",
        "supplier": payload["supplier"],
        "company": payload["company"],
        "transaction_date": payload["transaction_date"],
        "schedule_date": payload["schedule_date"],
        "currency": payload["currency"],
        "buying_price_list": payload["buying_price_list"],
        "conversion_rate": 1,
        "items": [],
    }
    items: list[dict[str, Any]] = []
    for raw_item in payload["items"]:
        item: dict[str, Any] = {
            "item_code": raw_item["item_code"],
            "qty": raw_item["qty"],
            "uom": raw_item["uom"],
            "conversion_factor": 1,
            "rate": raw_item["rate"],
            "warehouse": raw_item["warehouse"],
            "schedule_date": raw_item["schedule_date"],
        }
        for optional in ("description", "material_request"):
            if optional in raw_item:
                item[optional] = raw_item[optional]
        items.append(item)
    values["items"] = items
    return values


def purchase_order_calculation(action: ProposedAction) -> dict[str, Any]:
    """Return server-derived amount evidence for the approval presentation.

    ``rate`` is only accepted after the policy gate matches ERPNext's current
    Item Price.  The amount shown before approval is therefore derived from the
    typed quantity and approved rate, without trusting a client-supplied total
    or mutating the signed payload.
    """

    if action.action_type != "CREATE_PO_DRAFT":
        raise GatewayFault("INVALID_INPUT", "only CREATE_PO_DRAFT is supported", 400)
    line_amounts: list[str] = []
    total = Decimal("0")
    for item in action.payload["items"]:
        amount = _decimal(item["qty"], "qty") * _decimal(item["rate"], "rate")
        line_amounts.append(format(amount.normalize(), "f"))
        total += amount
    return {
        "currency": str(action.payload["currency"]),
        "line_amounts": line_amounts,
        "total_amount": format(total.normalize(), "f"),
        "basis": "qty * rate; rate must match ERPNext Item Price",
    }


def execution_key(action: ProposedAction) -> ExecutionKey:
    """Return the action/scope/digest tuple used by reservation uniqueness."""

    if action.action_type not in TARGET_DOCTYPES:
        raise GatewayFault("INVALID_INPUT", "unsupported governed action", 400)
    items = action.payload.get("items", [])
    warehouses = {str(item["warehouse"]) for item in items if item.get("warehouse")}
    source_ref = str(action.payload.get("source_name") or "")
    if not warehouses and not source_ref:
        raise GatewayFault("INVALID_INPUT", "execution scope is incomplete", 400)
    if len(warehouses) > 1:
        raise GatewayFault("INVALID_INPUT", "execution requires one warehouse scope", 400)
    return ExecutionKey(
        action_type=action.action_type,
        target_doctype=TARGET_DOCTYPES[action.action_type],
        company=str(action.payload["company"]),
        warehouse=next(iter(warehouses), source_ref),
        proposal_digest=action.proposal_digest,
        idempotency_key=action.idempotency_key,
    )


def p2p_values(action: ProposedAction) -> dict[str, Any]:
    """Build whitelisted ERP values for a P2P draft action.

    Source links are persisted in the child rows so ERPNext remains the
    authority for quantities, taxes, accounting and stock effects.
    """

    payload = action.payload
    if action.action_type == "CREATE_PR_DRAFT":
        return {
            "doctype": "Purchase Receipt",
            "company": payload["company"],
            "posting_date": payload["transaction_date"],
            "items": [
                {
                    "item_code": item["item_code"],
                    "qty": item["qty"],
                    "uom": item["uom"],
                    "warehouse": item["warehouse"],
                    "purchase_order": payload["source_name"],
                    "po_detail": item["source_row"],
                }
                for item in payload["items"]
            ],
        }
    if action.action_type == "CREATE_PI_DRAFT":
        return {
            "doctype": "Purchase Invoice",
            "company": payload["company"],
            "posting_date": payload["transaction_date"],
            "supplier": payload.get("supplier") or "",
            "items": [
                {
                    "item_code": item["item_code"],
                    "qty": item["qty"],
                    "rate": item["rate"],
                    "purchase_receipt": payload["source_name"],
                    "pr_detail": item["source_row"],
                }
                for item in payload["items"]
            ],
        }
    if action.action_type == "CREATE_PAYMENT_ENTRY_DRAFT":
        return {
            "doctype": "Payment Entry",
            "company": payload["company"],
            "payment_type": payload["payment_type"],
            "posting_date": payload["posting_date"],
            "party_type": payload["party_type"],
            "party": payload["party"],
            "paid_from": payload["paid_from"],
            "paid_to": payload["paid_to"],
            "paid_amount": payload["paid_amount"],
            "received_amount": payload["received_amount"],
            "source_exchange_rate": 1,
            "target_exchange_rate": 1,
            "references": payload["references"],
        }
    raise GatewayFault("INVALID_INPUT", "action does not create a P2P draft", 400)


def p2p_read_back(action: ProposedAction, doc: object) -> dict[str, Any]:
    """Verify a P2P target using only fields in the approved action."""

    expected_target = TARGET_DOCTYPES[action.action_type]
    actual_doctype = str(_value(doc, "doctype", expected_target) or expected_target)
    if actual_doctype != expected_target:
        raise ReadBackMismatch("target DocType does not match action")
    expected_status = 0
    if action.action_type.startswith("SUBMIT_"):
        expected_status = 1
    if action.action_type.startswith("CANCEL_"):
        expected_status = 2
    if _value(doc, "docstatus") != expected_status:
        raise ReadBackMismatch("target status does not match action")
    payload = action.payload
    for field in ("company",):
        _same_text(_value(doc, field), payload[field], field)
    verified: dict[str, Any] = {
        "doctype": expected_target,
        "docstatus": expected_status,
        "company": str(_value(doc, "company")),
    }
    actual_name = str(_value(doc, "name") or "")
    if (
        actual_name
        and action.action_type.startswith(("SUBMIT_", "CANCEL_"))
        and actual_name != str(payload.get("source_name") or "")
    ):
        raise ReadBackMismatch("target name does not match source document")
    if action.action_type in {"SUBMIT_PO", "CANCEL_PO"}:
        verified["source_name"] = str(payload["source_name"])
    elif action.action_type in {
        "SUBMIT_PR",
        "CANCEL_PR",
        "SUBMIT_PI",
        "CANCEL_PI",
        "SUBMIT_PAYMENT_ENTRY",
        "CANCEL_PAYMENT_ENTRY",
    }:
        verified["source_name"] = str(payload["source_name"])
    elif action.action_type in {"CREATE_PR_DRAFT", "CREATE_PI_DRAFT"}:
        rows = _value(doc, "items", [])
        if not isinstance(rows, (list, tuple)) or len(rows) != len(payload["items"]):
            raise ReadBackMismatch("P2P draft item count does not match")
        _same_text(
            _value(doc, "posting_date"),
            payload["transaction_date"],
            "posting_date",
        )
        verified["source_name"] = str(payload["source_name"])
        verified["posting_date"] = str(_value(doc, "posting_date"))
        verified["items_count"] = len(rows)
        source_link = (
            "purchase_order_item" if action.action_type == "CREATE_PR_DRAFT" else "pr_detail"
        )
        parent_link = (
            "purchase_order" if action.action_type == "CREATE_PR_DRAFT" else "purchase_receipt"
        )
        expected_by_source = {item["source_row"]: item for item in payload["items"]}
        seen_sources: set[str] = set()
        for index, actual in enumerate(rows):
            source_row = str(_value(actual, source_link) or "")
            expected = expected_by_source.get(source_row)
            if expected is None or source_row in seen_sources:
                raise ReadBackMismatch("P2P draft source rows do not match")
            seen_sources.add(source_row)
            actual_code = str(_value(actual, "item_code") or "")
            if actual_code != expected["item_code"]:
                raise ReadBackMismatch(f"item_{index}.item_code does not match")
            _same_text(
                source_row,
                expected["source_row"],
                f"item_{index}.{source_link}",
            )
            _same_text(
                _value(actual, parent_link),
                payload["source_name"],
                f"item_{index}.{parent_link}",
            )
            if expected.get("warehouse") is not None:
                _same_text(
                    _value(actual, "warehouse"),
                    expected["warehouse"],
                    f"item_{index}.warehouse",
                )
            if expected.get("uom") is not None:
                _same_text(_value(actual, "uom"), expected["uom"], f"item_{index}.uom")
            actual_qty = _decimal(_value(actual, "qty"), f"item_{index}.qty")
            if actual_qty != _decimal(expected["qty"], f"item_{index}.qty"):
                raise ReadBackMismatch(f"item_{index}.qty does not match")
            verified[f"item_{index}.item_code"] = actual_code
            verified[f"item_{index}.qty"] = format(actual_qty.normalize(), "f")
            verified[f"item_{index}.{source_link}"] = str(_value(actual, source_link))
            verified[f"item_{index}.{parent_link}"] = str(_value(actual, parent_link))
            verified[f"item_{index}.warehouse"] = str(_value(actual, "warehouse") or "")
            verified[f"item_{index}.uom"] = str(_value(actual, "uom") or "")
            if action.action_type == "CREATE_PI_DRAFT":
                actual_rate = _decimal(_value(actual, "rate"), f"item_{index}.rate")
                expected_rate = _decimal(expected["rate"], f"item_{index}.rate")
                if actual_rate != expected_rate:
                    raise ReadBackMismatch(f"item_{index}.rate does not match")
                actual_amount = _decimal(_value(actual, "amount"), f"item_{index}.amount")
                if actual_amount != actual_qty * actual_rate:
                    raise ReadBackMismatch(
                        f"item_{index}.amount does not match qty multiplied by rate"
                    )
                verified[f"item_{index}.rate"] = format(actual_rate.normalize(), "f")
                verified[f"item_{index}.amount"] = format(actual_amount.normalize(), "f")
        if seen_sources != set(expected_by_source):
            raise ReadBackMismatch("P2P draft source rows do not match")
    elif action.action_type == "CREATE_PAYMENT_ENTRY_DRAFT":
        for field in ("party_type", "party", "payment_type", "paid_from", "paid_to"):
            _same_text(_value(doc, field), payload[field], field)
        actual_paid = _decimal(_value(doc, "paid_amount"), "paid_amount")
        if actual_paid != _decimal(payload["paid_amount"], "paid_amount"):
            raise ReadBackMismatch("paid_amount does not match")
        verified.update(
            {
                "party_type": str(_value(doc, "party_type")),
                "party": str(_value(doc, "party")),
                "payment_type": str(_value(doc, "payment_type")),
                "paid_amount": format(actual_paid.normalize(), "f"),
                "references_count": len(payload["references"]),
            }
        )
    return verified


def p2p_receipt_evidence_matches(
    action: ProposedAction,
    recorded: Mapping[str, Any],
    current: Mapping[str, Any],
) -> bool:
    """Compare a receipt with a fresh read while allowing later P2P progress.

    Invoice outstanding and cumulative source billing change when a later
    payment or invoice is legitimately posted.  The immutable PI totals,
    source links, and balanced GL evidence still have to remain present; the
    mutable values stay in the receipt as the historical post-submit snapshot.
    """

    if action.action_type != "SUBMIT_PI":
        return dict(recorded) == dict(current)

    def stable(items: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in items.items()
            if key not in _PI_MUTABLE_RECEIPT_FIELDS
            and not key.endswith((".pr_billed_amt", ".pr_per_billed", ".po_per_billed"))
        }

    recorded_stable = stable(recorded)
    current_stable = stable(current)
    return recorded_stable == current_stable


def _value(source: object, field: str, default: object = None) -> object:
    if isinstance(source, Mapping):
        return source.get(field, default)
    return getattr(source, field, default)


def _decimal(value: object, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ReadBackMismatch(f"{field} is not numeric") from error
    if not number.is_finite():
        raise ReadBackMismatch(f"{field} is not finite")
    return number


def _same_text(actual: object, expected: object, field: str) -> None:
    if str(actual or "") != str(expected or ""):
        raise ReadBackMismatch(f"{field} does not match approved payload")


def verify_material_request_read_back(action: ProposedAction, doc: object) -> dict[str, Any]:
    """Verify critical approved fields and return scalar receipt evidence."""

    values = material_request_values(action)
    if _value(doc, "docstatus") != 0:
        raise ReadBackMismatch("Material Request is not a Draft")
    _same_text(_value(doc, "company"), values["company"], "company")
    _same_text(
        _value(doc, "material_request_type"),
        values["material_request_type"],
        "material_request_type",
    )
    _same_text(_value(doc, "transaction_date"), values["transaction_date"], "transaction_date")
    raw_actual_items = _value(doc, "items", [])
    if not isinstance(raw_actual_items, (list, tuple)):
        raise ReadBackMismatch("items are not a sequence")
    actual_items = list(raw_actual_items)
    expected_items = list(values["items"])
    if len(actual_items) != len(expected_items):
        raise ReadBackMismatch("item count does not match approved payload")

    verified: dict[str, Any] = {
        "docstatus": 0,
        "company": str(_value(doc, "company")),
        "material_request_type": str(_value(doc, "material_request_type")),
        "transaction_date": str(_value(doc, "transaction_date")),
        "items_count": len(actual_items),
    }
    for index, (actual, expected) in enumerate(zip(actual_items, expected_items, strict=True)):
        prefix = f"item_{index}"
        for field in ("item_code", "warehouse", "schedule_date"):
            _same_text(_value(actual, field), expected[field], f"{prefix}.{field}")
            verified[f"{prefix}.{field}"] = str(_value(actual, field))
        actual_qty = _decimal(_value(actual, "qty"), f"{prefix}.qty")
        expected_qty = _decimal(expected["qty"], f"{prefix}.qty")
        if actual_qty != expected_qty:
            raise ReadBackMismatch(f"{prefix}.qty does not match approved payload")
        verified[f"{prefix}.qty"] = format(actual_qty.normalize(), "f")
        actual_uom = str(_value(actual, "uom") or "")
        if expected.get("uom") is not None:
            _same_text(actual_uom, expected["uom"], f"{prefix}.uom")
        verified[f"{prefix}.uom"] = actual_uom
        if expected.get("description") is not None:
            _same_text(
                _value(actual, "description"),
                expected["description"],
                f"{prefix}.description",
            )
    return verified


def verify_purchase_order_read_back(action: ProposedAction, doc: object) -> dict[str, Any]:
    """Verify critical PO Draft fields and return scalar receipt evidence."""

    values = purchase_order_values(action)
    if _value(doc, "docstatus") != 0:
        raise ReadBackMismatch("Purchase Order is not a Draft")
    for field in ("supplier", "company", "currency", "buying_price_list", "transaction_date"):
        _same_text(_value(doc, field), values[field], field)
    actual_conversion_rate = _decimal(_value(doc, "conversion_rate"), "conversion_rate")
    expected_conversion_rate = _decimal(values["conversion_rate"], "conversion_rate")
    if actual_conversion_rate != expected_conversion_rate:
        raise ReadBackMismatch("conversion_rate does not match approved payload")
    _same_text(_value(doc, "schedule_date"), values["schedule_date"], "schedule_date")
    raw_actual_items = _value(doc, "items", [])
    if not isinstance(raw_actual_items, (list, tuple)):
        raise ReadBackMismatch("items are not a sequence")
    actual_items = list(raw_actual_items)
    expected_items = list(values["items"])
    if len(actual_items) != len(expected_items):
        raise ReadBackMismatch("item count does not match approved payload")

    verified: dict[str, Any] = {
        "docstatus": 0,
        "supplier": str(_value(doc, "supplier")),
        "company": str(_value(doc, "company")),
        "currency": str(_value(doc, "currency")),
        "buying_price_list": str(_value(doc, "buying_price_list")),
        "transaction_date": str(_value(doc, "transaction_date")),
        "conversion_rate": format(actual_conversion_rate.normalize(), "f"),
        "schedule_date": str(_value(doc, "schedule_date")),
        "items_count": len(actual_items),
    }
    for index, (actual, expected) in enumerate(zip(actual_items, expected_items, strict=True)):
        prefix = f"item_{index}"
        for field in ("item_code", "warehouse", "schedule_date", "uom"):
            _same_text(_value(actual, field), expected[field], f"{prefix}.{field}")
            verified[f"{prefix}.{field}"] = str(_value(actual, field))
        actual_conversion = _decimal(
            _value(actual, "conversion_factor"), f"{prefix}.conversion_factor"
        )
        expected_conversion = _decimal(expected["conversion_factor"], f"{prefix}.conversion_factor")
        if actual_conversion != expected_conversion:
            raise ReadBackMismatch(f"{prefix}.conversion_factor does not match approved payload")
        verified[f"{prefix}.conversion_factor"] = format(actual_conversion.normalize(), "f")
        actual_qty = _decimal(_value(actual, "qty"), f"{prefix}.qty")
        expected_qty = _decimal(expected["qty"], f"{prefix}.qty")
        if actual_qty != expected_qty:
            raise ReadBackMismatch(f"{prefix}.qty does not match approved payload")
        verified[f"{prefix}.qty"] = format(actual_qty.normalize(), "f")
        actual_rate = _decimal(_value(actual, "rate"), f"{prefix}.rate")
        expected_rate = _decimal(expected["rate"], f"{prefix}.rate")
        if actual_rate != expected_rate:
            raise ReadBackMismatch(f"{prefix}.rate does not match approved payload")
        verified[f"{prefix}.rate"] = format(actual_rate.normalize(), "f")
        actual_amount = _decimal(_value(actual, "amount"), f"{prefix}.amount")
        expected_amount = actual_qty * actual_rate
        if actual_amount != expected_amount:
            raise ReadBackMismatch(f"{prefix}.amount does not match qty multiplied by rate")
        verified[f"{prefix}.amount"] = format(actual_amount.normalize(), "f")
        expected_material_request = expected.get("material_request")
        actual_material_request = str(_value(actual, "material_request") or "")
        if expected_material_request is not None:
            _same_text(
                actual_material_request,
                expected_material_request,
                f"{prefix}.material_request",
            )
        elif actual_material_request:
            raise ReadBackMismatch(
                f"{prefix}.material_request was added outside the approved payload"
            )
        verified[f"{prefix}.material_request"] = actual_material_request
        if expected.get("description") is not None:
            _same_text(
                _value(actual, "description"),
                expected["description"],
                f"{prefix}.description",
            )
    return verified


def map_execution_error(error: BaseException) -> tuple[str, str, int]:
    """Map a controller or governance failure to a stable public category."""

    if isinstance(error, GatewayFault):
        if error.code in {"PERMISSION_DENIED", "ERP_PERMISSION_ERROR"}:
            return "ERP_PERMISSION_ERROR", error.code, 403
        if error.code in {"CONFLICT", "STALE", "POLICY_REJECTED", "EXPIRED"}:
            return "CONFLICT", error.code, 409
        if error.code in {"UNCERTAIN_RESULT", "TIMEOUT"}:
            return "UNCERTAIN_RESULT", error.code, 503
        return "ERP_VALIDATION_ERROR", error.code, error.status_code
    name = type(error).__name__
    if name in {"PermissionError", "NotPermittedError"}:
        return "ERP_PERMISSION_ERROR", name, 403
    if name == "DoesNotExistError":
        return "ERP_NOT_FOUND", name, 404
    if name in {
        "ValidationError",
        "MandatoryError",
        "LinkValidationError",
        "InvalidStatusError",
        "UniqueValidationError",
        "ReadBackMismatch",
        "TimestampMismatchError",
    }:
        return "ERP_VALIDATION_ERROR", name, 422
    return "UNCERTAIN_RESULT", "UNEXPECTED_EXECUTION_ERROR", 503


__all__ = [
    "ExecutionKey",
    "ReadBackMismatch",
    "ReconciliationClassification",
    "classify_reconciliation",
    "execution_key",
    "map_execution_error",
    "material_request_values",
    "p2p_receipt_evidence_matches",
    "purchase_order_values",
    "verify_material_request_read_back",
    "verify_purchase_order_read_back",
]
