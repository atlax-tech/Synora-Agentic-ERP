"""Pure contracts for the Phase 6 governed Draft executors."""

from typing import Any

import pytest

from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.governance.contracts import build_proposed_action
from synora_agentic_erp.governance.execution_contracts import (
    ReadBackMismatch,
    classify_reconciliation,
    execution_key,
    map_execution_error,
    material_request_values,
    purchase_order_values,
    verify_material_request_read_back,
    verify_purchase_order_read_back,
)


def _action() -> Any:
    return build_proposed_action(
        {
            "schema_version": "1",
            "action_type": "CREATE_MR_DRAFT",
            "run_id": "11111111-1111-4111-8111-111111111111",
            "action_id": "22222222-2222-4222-8222-222222222222",
            "initiator": "buyer@example.test",
            "payload": {
                "company": "SYNORA Test Company",
                "transaction_date": "2026-08-27",
                "material_request_type": "Purchase",
                "items": [
                    {
                        "item_code": "ITEM-001",
                        "qty": "2",
                        "uom": "Nos",
                        "schedule_date": "2026-09-01",
                        "warehouse": "Stores - ST",
                    }
                ],
            },
            "evidence_refs": ["obs:stock-001"],
            "calculation_refs": ["calc:shortage-001"],
            "risk_class": "MEDIUM",
            "approval_class": "INITIATOR_CONFIRMATION",
            "snapshot_ref": "snapshot:run-001",
            "idempotency_key": "p6-mr-20260827-0001",
            "expires_at": "2030-01-01T00:00:00+00:00",
            "revalidation_rule": "FULL_PRE_EXECUTE_RECHECK_V1",
            "summary": "Create a material request draft",
            "correlation_id": "33333333-3333-4333-8333-333333333333",
        }
    )


def _document(*, qty: object = "2", docstatus: int = 0) -> dict[str, object]:
    return {
        "docstatus": docstatus,
        "company": "SYNORA Test Company",
        "material_request_type": "Purchase",
        "transaction_date": "2026-08-27",
        "items": [
            {
                "item_code": "ITEM-001",
                "qty": qty,
                "uom": "Nos",
                "schedule_date": "2026-09-01",
                "warehouse": "Stores - ST",
            }
        ],
    }


def _po_action() -> Any:
    raw = _action().to_dict()
    raw["action_type"] = "CREATE_PO_DRAFT"
    raw["idempotency_key"] = "p6-po-20260827-0001"
    raw["payload"] = {
        "company": "SYNORA Test Company",
        "supplier": "SUPPLIER-001",
        "transaction_date": "2026-08-27",
        "schedule_date": "2026-09-01",
        "currency": "CNY",
        "buying_price_list": "Buying CNY",
        "items": [
            {
                "item_code": "ITEM-001",
                "qty": "2",
                "uom": "Nos",
                "rate": "100",
                "schedule_date": "2026-09-01",
                "warehouse": "Stores - ST",
            }
        ],
    }
    raw.pop("proposal_digest", None)
    return build_proposed_action(raw)


def _po_document(*, rate: object = "100", amount: object = "200") -> dict[str, object]:
    return {
        "docstatus": 0,
        "supplier": "SUPPLIER-001",
        "company": "SYNORA Test Company",
        "currency": "CNY",
        "buying_price_list": "Buying CNY",
        "conversion_rate": 1,
        "transaction_date": "2026-08-27",
        "schedule_date": "2026-09-01",
        "items": [
            {
                "item_code": "ITEM-001",
                "qty": "2.000",
                "uom": "Nos",
                "rate": rate,
                "amount": amount,
                "conversion_factor": 1,
                "schedule_date": "2026-09-01",
                "warehouse": "Stores - ST",
            }
        ],
    }


def test_writer_reconstructs_only_the_approved_material_request_payload() -> None:
    action = _action()
    values = material_request_values(action)

    assert values == {
        "doctype": "Material Request",
        "naming_series": "MAT-MR-.YYYY.-",
        "material_request_type": "Purchase",
        "company": "SYNORA Test Company",
        "transaction_date": "2026-08-27",
        "items": [
            {
                "item_code": "ITEM-001",
                "qty": "2",
                "warehouse": "Stores - ST",
                "schedule_date": "2026-09-01",
                "uom": "Nos",
            }
        ],
    }
    assert execution_key(action).as_tuple() == (
        "CREATE_MR_DRAFT",
        "Material Request",
        "SYNORA Test Company",
        "Stores - ST",
        action.proposal_digest,
        "p6-mr-20260827-0001",
    )


def test_po_writer_reconstructs_supplier_price_and_amount_inputs() -> None:
    action = _po_action()
    values = purchase_order_values(action)

    assert values == {
        "doctype": "Purchase Order",
        "naming_series": "PUR-ORD-.YYYY.-",
        "supplier": "SUPPLIER-001",
        "company": "SYNORA Test Company",
        "transaction_date": "2026-08-27",
        "schedule_date": "2026-09-01",
        "currency": "CNY",
        "buying_price_list": "Buying CNY",
        "conversion_rate": 1,
        "items": [
            {
                "item_code": "ITEM-001",
                "qty": "2",
                "uom": "Nos",
                "conversion_factor": 1,
                "rate": "100",
                "warehouse": "Stores - ST",
                "schedule_date": "2026-09-01",
            }
        ],
    }
    assert execution_key(action).as_tuple() == (
        "CREATE_PO_DRAFT",
        "Purchase Order",
        "SYNORA Test Company",
        "Stores - ST",
        action.proposal_digest,
        "p6-po-20260827-0001",
    )


def test_po_read_back_verifies_amount_as_qty_times_rate() -> None:
    verified = verify_purchase_order_read_back(_po_action(), _po_document())

    assert verified["docstatus"] == 0
    assert verified["supplier"] == "SUPPLIER-001"
    assert verified["currency"] == "CNY"
    assert verified["item_0.qty"] == "2"
    assert verified["item_0.rate"] == "100"
    assert verified["item_0.amount"] == "200"


@pytest.mark.parametrize(
    "document",
    [
        {**_po_document(), "docstatus": 1},
        {**_po_document(), "currency": "USD"},
        _po_document(rate="99", amount="198"),
        _po_document(rate="100", amount="201"),
    ],
)
def test_po_read_back_rejects_supplier_currency_rate_or_amount_drift(
    document: dict[str, object],
) -> None:
    with pytest.raises(ReadBackMismatch):
        verify_purchase_order_read_back(_po_action(), document)


def test_po_contract_rejects_missing_uom_and_zero_rate() -> None:
    raw = _po_action().to_dict()
    payload = dict(raw["payload"])
    item = dict(payload["items"][0])
    item.pop("uom")
    payload["items"] = [item]
    raw["payload"] = payload
    raw.pop("proposal_digest", None)
    with pytest.raises(GatewayFault):
        build_proposed_action(raw)

    raw = _po_action().to_dict()
    payload = dict(raw["payload"])
    item = dict(payload["items"][0])
    item["rate"] = "0"
    payload["items"] = [item]
    raw["payload"] = payload
    raw.pop("proposal_digest", None)
    with pytest.raises(GatewayFault):
        build_proposed_action(raw)


def test_read_back_returns_scalar_receipt_evidence_and_normalizes_quantity() -> None:
    verified = verify_material_request_read_back(_action(), _document(qty="2.000"))

    assert verified == {
        "docstatus": 0,
        "company": "SYNORA Test Company",
        "material_request_type": "Purchase",
        "transaction_date": "2026-08-27",
        "items_count": 1,
        "item_0.item_code": "ITEM-001",
        "item_0.warehouse": "Stores - ST",
        "item_0.schedule_date": "2026-09-01",
        "item_0.qty": "2",
        "item_0.uom": "Nos",
    }


@pytest.mark.parametrize(
    "document",
    [
        _document(docstatus=1),
        {**_document(), "company": "Other Company"},
        _document(qty="3"),
    ],
)
def test_read_back_rejects_any_critical_field_drift(document: dict[str, object]) -> None:
    with pytest.raises(ReadBackMismatch):
        verify_material_request_read_back(_action(), document)


def test_error_mapping_is_stable_for_permission_validation_and_uncertainty() -> None:
    assert map_execution_error(GatewayFault("PERMISSION_DENIED", "denied", 403)) == (
        "ERP_PERMISSION_ERROR",
        "PERMISSION_DENIED",
        403,
    )
    assert map_execution_error(GatewayFault("CONFLICT", "stale", 409)) == (
        "CONFLICT",
        "CONFLICT",
        409,
    )
    assert map_execution_error(ValueError("controller rejected")) == (
        "UNCERTAIN_RESULT",
        "UNEXPECTED_EXECUTION_ERROR",
        503,
    )


def test_execution_key_rejects_multiple_warehouse_scope() -> None:
    raw = _action().to_dict()
    payload = dict(raw["payload"])
    payload["items"] = [
        *payload["items"],
        {
            "item_code": "ITEM-002",
            "qty": "1",
            "uom": "Nos",
            "schedule_date": "2026-09-01",
            "warehouse": "Other Stores",
        },
    ]
    raw["payload"] = payload
    raw.pop("proposal_digest", None)
    action = build_proposed_action(raw)
    with pytest.raises(GatewayFault) as error:
        execution_key(action)
    assert error.value.code == "INVALID_INPUT"


@pytest.mark.parametrize(
    ("counts", "lease_expired", "evidence", "expected"),
    [
        ((1, 1), False, False, "RECONCILED_SUCCESS"),
        ((2, 1), True, True, "MANUAL_INTERVENTION"),
        ((0, 0), False, False, "RECONCILIATION_REQUIRED"),
        ((0, 0), True, True, "RECONCILED_FAILURE"),
        ((0, 0), True, False, "MANUAL_INTERVENTION"),
    ],
)
def test_reconciliation_classification_never_allows_retry(
    counts: tuple[int, int], lease_expired: bool, evidence: bool, expected: str
) -> None:
    result = classify_reconciliation(
        candidate_count=counts[0],
        matching_count=counts[1],
        lease_expired=lease_expired,
        failure_evidence_complete=evidence,
    )
    assert result.result_status == expected
    assert result.can_retry is False
