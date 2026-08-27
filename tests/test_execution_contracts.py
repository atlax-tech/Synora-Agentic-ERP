"""Pure contracts for the Phase 6 Material Request Draft executor."""

from typing import Any

import pytest

from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.governance.contracts import build_proposed_action
from synora_agentic_erp.governance.execution_contracts import (
    ReadBackMismatch,
    execution_key,
    map_execution_error,
    material_request_values,
    verify_material_request_read_back,
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
