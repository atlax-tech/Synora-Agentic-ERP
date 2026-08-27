"""Pure contract tests for the Phase 6 governed-action record boundary."""

from datetime import UTC, datetime

import pytest

from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.governance.contracts import (
    build_proposed_action,
    canonical_proposal_bytes,
    create_execution_receipt,
    parse_json_object,
)

RUN_ID = "11111111-1111-4111-8111-111111111111"
ACTION_ID = "22222222-2222-4222-8222-222222222222"
CORRELATION_ID = "33333333-3333-4333-8333-333333333333"
EXPIRY = datetime(2030, 1, 1, tzinfo=UTC).isoformat()


def _action(*, summary: str = "Create a purchase request") -> dict[str, object]:
    return {
        "schema_version": "1",
        "action_type": "CREATE_MR_DRAFT",
        "run_id": RUN_ID,
        "action_id": ACTION_ID,
        "initiator": "buyer@example.test",
        "payload": {
            "company": "SYNORA Test Company",
            "transaction_date": "2026-08-27",
            "material_request_type": "Purchase",
            "items": [
                {
                    "item_code": "ITEM-001",
                    "qty": "2.00",
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
        "expires_at": EXPIRY,
        "revalidation_rule": "FULL_PRE_EXECUTE_RECHECK_V1",
        "summary": summary,
        "correlation_id": CORRELATION_ID,
    }


def test_proposed_action_digest_is_stable_and_excludes_display_summary() -> None:
    first = build_proposed_action(_action(summary="first explanation"))
    second = build_proposed_action(_action(summary="different explanation"))

    assert first.proposal_digest == second.proposal_digest
    assert canonical_proposal_bytes(first).startswith(b'{"action_id":"22222222')
    # Golden value is intentionally fixed once the canonical contract is implemented.
    assert (
        first.proposal_digest == "fccbd31758106e533eec13b4083f4e49ab4565346d54834aa43fa6baa18deb0b"
    )


def test_contract_rejects_unknown_fields_and_invalid_action_shape() -> None:
    unknown = _action()
    unknown["unexpected"] = "must fail"
    with pytest.raises(GatewayFault) as error:
        build_proposed_action(unknown)
    assert error.value.code == "INVALID_INPUT"

    bad_payload = _action()
    bad_payload["payload"] = {**bad_payload["payload"], "items": []}
    with pytest.raises(GatewayFault):
        build_proposed_action(bad_payload)


def test_json_parser_rejects_duplicate_keys_and_non_finite_numbers() -> None:
    with pytest.raises(GatewayFault):
        parse_json_object('{"schema_version":"1","schema_version":"1"}')
    with pytest.raises(GatewayFault):
        parse_json_object('{"qty":NaN}')


def test_typed_decimal_and_date_values_are_canonicalized() -> None:
    action = _action()
    action["payload"] = {
        **action["payload"],
        "items": [
            {
                "item_code": "ITEM-001",
                "qty": "2.0",
                "uom": "Nos",
                "schedule_date": "2026-09-01",
                "warehouse": "Stores - ST",
            }
        ],
    }
    parsed = build_proposed_action(action)
    assert parsed.payload["items"][0]["qty"] == "2"
    assert parsed.payload["transaction_date"] == "2026-08-27"


def test_execution_success_receipt_requires_verified_target_and_fields() -> None:
    common = {
        "receipt_id": "44444444-4444-4444-8444-444444444444",
        "action_id": ACTION_ID,
        "run_id": RUN_ID,
        "idempotency_key": "p6-mr-20260827-0001",
        "initiator": "buyer@example.test",
        "executor": "buyer@example.test",
        "proposal_digest": "a" * 64,
        "response_category": "ERP_SUCCESS",
        "failure_category": None,
        "final_state": "SUCCEEDED",
        "started_at": EXPIRY,
        "completed_at": EXPIRY,
        "correlation_id": CORRELATION_ID,
    }
    with pytest.raises(GatewayFault):
        create_execution_receipt({**common, "target_doctype": None, "target_name": None})
    with pytest.raises(GatewayFault):
        create_execution_receipt(
            {
                **common,
                "target_doctype": "Material Request",
                "target_name": "MAT-REQ-0001",
                "verified_fields": {},
            }
        )

    receipt = create_execution_receipt(
        {
            **common,
            "target_doctype": "Material Request",
            "target_name": "MAT-REQ-0001",
            "verified_fields": {"docstatus": 0, "company": "SYNORA Test Company"},
        }
    )
    assert receipt.final_state == "SUCCEEDED"
