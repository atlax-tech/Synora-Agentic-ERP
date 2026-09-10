from __future__ import annotations

from uuid import uuid4

import pytest

from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.governance.contracts import (
    P2P_REVALIDATION_RULE,
    P2P_SCHEMA_VERSION,
    TARGET_DOCTYPES,
    build_proposed_action,
)


def _base(action_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": P2P_SCHEMA_VERSION,
        "action_type": action_type,
        "run_id": str(uuid4()),
        "action_id": str(uuid4()),
        "initiator": "buyer@example.com",
        "payload": payload,
        "evidence_refs": ["trace:p10"],
        "calculation_refs": ["calc:p10"],
        "risk_class": "HIGH",
        "approval_class": "INDEPENDENT_APPROVER",
        "snapshot_ref": "snapshot:p10",
        "idempotency_key": "p10-contract-001",
        "expires_at": "2099-01-01T00:00:00Z",
        "revalidation_rule": P2P_REVALIDATION_RULE,
        "correlation_id": str(uuid4()),
    }


@pytest.mark.parametrize(
    ("action_type", "payload"),
    [
        (
            "SUBMIT_PO",
            {
                "company": "Company A",
                "source_doctype": "Purchase Order",
                "source_name": "PUR-ORD-0001",
            },
        ),
        (
            "CREATE_PR_DRAFT",
            {
                "company": "Company A",
                "source_doctype": "Purchase Order",
                "source_name": "PUR-ORD-0001",
                "transaction_date": "2026-09-10",
                "items": [
                    {
                        "source_row": "row-1",
                        "item_code": "ITEM-1",
                        "qty": "2",
                        "uom": "Nos",
                        "warehouse": "Stores - A",
                    }
                ],
            },
        ),
        (
            "CREATE_PI_DRAFT",
            {
                "company": "Company A",
                "source_doctype": "Purchase Receipt",
                "source_name": "MAT-PRE-0001",
                "transaction_date": "2026-09-10",
                "items": [
                    {
                        "source_row": "row-1",
                        "item_code": "ITEM-1",
                        "qty": "2",
                        "uom": "Nos",
                        "warehouse": "Stores - A",
                        "rate": "10",
                    }
                ],
            },
        ),
        (
            "CREATE_PAYMENT_ENTRY_DRAFT",
            {
                "company": "Company A",
                "source_doctype": "Purchase Invoice",
                "source_name": "ACC-PINV-0001",
                "party_type": "Supplier",
                "party": "Supplier A",
                "payment_type": "Pay",
                "posting_date": "2026-09-10",
                "paid_from": "Creditors - A",
                "paid_to": "Bank - A",
                "paid_amount": "10",
                "received_amount": "10",
                "references": [
                    {
                        "reference_doctype": "Purchase Invoice",
                        "reference_name": "ACC-PINV-0001",
                        "allocated_amount": "10",
                    }
                ],
            },
        ),
    ],
)
def test_phase10_actions_are_versioned_and_independently_approved(
    action_type: str, payload: dict[str, object]
) -> None:
    action = build_proposed_action(_base(action_type, payload))
    assert action.schema_version == P2P_SCHEMA_VERSION
    assert action.approval_class == "INDEPENDENT_APPROVER"
    assert TARGET_DOCTYPES[action_type] in {
        "Purchase Order",
        "Purchase Receipt",
        "Purchase Invoice",
        "Payment Entry",
    }
    assert len(action.proposal_digest) == 64


def test_phase10_action_rejects_legacy_schema() -> None:
    value = _base(
        "SUBMIT_PO",
        {
            "company": "Company A",
            "source_doctype": "Purchase Order",
            "source_name": "PUR-ORD-0001",
        },
    )
    value["schema_version"] = "1"
    with pytest.raises(GatewayFault):
        build_proposed_action(value)


def test_phase10_payment_rejects_unknown_fields() -> None:
    value = _base(
        "CREATE_PAYMENT_ENTRY_DRAFT",
        {
            "company": "Company A",
            "source_doctype": "Purchase Invoice",
            "source_name": "ACC-PINV-0001",
            "party_type": "Supplier",
            "party": "Supplier A",
            "payment_type": "Pay",
            "posting_date": "2026-09-10",
            "paid_from": "Creditors - A",
            "paid_to": "Bank - A",
            "paid_amount": "10",
            "received_amount": "10",
            "references": [],
            "bank_account": "client-controlled",
        },
    )
    with pytest.raises(GatewayFault):
        build_proposed_action(value)


def test_phase10_draft_rejects_duplicate_source_rows() -> None:
    value = _base(
        "CREATE_PR_DRAFT",
        {
            "company": "Company A",
            "source_doctype": "Purchase Order",
            "source_name": "PUR-ORD-0001",
            "transaction_date": "2026-09-10",
            "items": [
                {
                    "source_row": "row-1",
                    "item_code": "ITEM-1",
                    "qty": "1",
                    "uom": "Nos",
                    "warehouse": "Stores - A",
                },
                {
                    "source_row": "row-1",
                    "item_code": "ITEM-1",
                    "qty": "1",
                    "uom": "Nos",
                    "warehouse": "Stores - A",
                },
            ],
        },
    )
    with pytest.raises(GatewayFault):
        build_proposed_action(value)
