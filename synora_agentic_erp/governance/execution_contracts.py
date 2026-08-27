"""Pure contracts for the first governed Material Request write."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.governance.contracts import ProposedAction


class ReadBackMismatch(ValueError):
    """The ERP document does not match the approved critical fields."""


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


def execution_key(action: ProposedAction) -> ExecutionKey:
    """Return the action/scope/digest tuple used by reservation uniqueness."""

    if action.action_type != "CREATE_MR_DRAFT":
        raise GatewayFault("INVALID_INPUT", "only CREATE_MR_DRAFT is supported", 400)
    items = action.payload["items"]
    warehouses = {str(item["warehouse"]) for item in items}
    if len(warehouses) != 1:
        raise GatewayFault("INVALID_INPUT", "MR execution requires one warehouse scope", 400)
    return ExecutionKey(
        action_type=action.action_type,
        target_doctype="Material Request",
        company=str(action.payload["company"]),
        warehouse=next(iter(warehouses)),
        proposal_digest=action.proposal_digest,
        idempotency_key=action.idempotency_key,
    )


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
    }:
        return "ERP_VALIDATION_ERROR", name, 422
    return "UNCERTAIN_RESULT", "UNEXPECTED_EXECUTION_ERROR", 503


__all__ = [
    "ExecutionKey",
    "ReadBackMismatch",
    "execution_key",
    "map_execution_error",
    "material_request_values",
    "verify_material_request_read_back",
]
