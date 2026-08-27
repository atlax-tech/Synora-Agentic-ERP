"""Frappe persistence and permission tests for Phase 6 governance records."""

from datetime import UTC, datetime
from uuid import uuid4

import frappe
from frappe.tests.utils import FrappeTestCase

from synora_agentic_erp.api import issue_run
from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.governance.contracts import (
    build_proposed_action,
    create_execution_receipt,
    parse_approval_decision,
    parse_policy_decision,
)
from synora_agentic_erp.governance.service import (
    persist_approval_decision,
    persist_execution_receipt,
    persist_policy_decision,
    persist_proposed_action,
    transition_action_state,
)

BUYER = "synora-p1-buyer@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"


def _future() -> str:
    return datetime(2030, 1, 1, tzinfo=UTC).isoformat()


def _action(run_id: str, *, action_id: str | None = None) -> dict[str, object]:
    return {
        "schema_version": "1",
        "action_type": "CREATE_MR_DRAFT",
        "run_id": run_id,
        "action_id": action_id or str(uuid4()),
        "initiator": BUYER,
        "payload": {
            "company": COMPANY,
            "transaction_date": "2026-08-27",
            "material_request_type": "Purchase",
            "items": [
                {
                    "item_code": "SYNORA-P1-Item-1001",
                    "qty": "2",
                    "uom": "Nos",
                    "schedule_date": "2026-09-01",
                    "warehouse": WAREHOUSE,
                }
            ],
        },
        "evidence_refs": ["observation:stock-1001"],
        "calculation_refs": ["calculation:shortage-1001"],
        "risk_class": "MEDIUM",
        "approval_class": "INITIATOR_CONFIRMATION",
        "snapshot_ref": "snapshot:phase6-test",
        "idempotency_key": f"p6-test-{uuid4().hex}",
        "expires_at": _future(),
        "revalidation_rule": "FULL_PRE_EXECUTE_RECHECK_V1",
        "summary": "Create a material request draft",
        "correlation_id": str(uuid4()),
    }


class TestGovernanceRecords(FrappeTestCase):
    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        super().tearDown()

    def _run(self) -> str:
        frappe.set_user(BUYER)
        result = issue_run(
            COMPANY,
            "ensure stock for SYNORA-P1-Item-1001",
            warehouse=WAREHOUSE,
            correlation_id=str(uuid4()),
        )
        self.assertTrue(result["ok"])
        return str(result["run"]["run_id"])

    def test_action_policy_approval_and_failure_receipt_persist_as_linked_facts(self) -> None:
        run_id = self._run()
        action = build_proposed_action(_action(run_id))
        stored = persist_proposed_action(action)
        self.assertEqual(stored["state"], "DRAFT")
        self.assertEqual(stored["state_version"], 1)

        policy = parse_policy_decision(
            {
                "decision_id": str(uuid4()),
                "action_id": action.action_id,
                "proposal_digest": action.proposal_digest,
                "actor": BUYER,
                "checks": {
                    "identity": "PASS",
                    "scope": "PASS",
                    "permission": "PASS",
                    "deterministic": "PASS",
                    "workflow_policy": "PASS",
                },
                "matched_rule": "P6-MAP-20260827-v1",
                "rule_version": "1",
                "outcome": "ALLOW",
                "reason": "fixed development mapping",
                "snapshot_ref": "snapshot:phase6-test",
                "expires_at": _future(),
                "decided_at": _future(),
                "correlation_id": str(uuid4()),
            }
        )
        self.assertEqual(persist_policy_decision(policy)["outcome"], "ALLOW")

        approval = parse_approval_decision(
            {
                "decision_id": str(uuid4()),
                "action_id": action.action_id,
                "proposal_digest": action.proposal_digest,
                "actor": BUYER,
                "decision": "ALLOW",
                "matched_rule": "P6-MAP-20260827-v1",
                "snapshot_ref": "snapshot:phase6-test",
                "expires_at": _future(),
                "reason": "initiator confirmation",
                "decided_at": _future(),
                "correlation_id": str(uuid4()),
            }
        )
        self.assertEqual(persist_approval_decision(approval)["decision"], "ALLOW")

        transitioned = transition_action_state(
            action.action_id,
            "AWAITING_APPROVAL",
            expected_version=1,
            reason="policy checks passed",
            correlation_id=str(uuid4()),
        )
        self.assertEqual(transitioned["state"], "AWAITING_APPROVAL")
        self.assertEqual(transitioned["state_version"], 2)

        receipt_input = {
            "receipt_id": str(uuid4()),
            "action_id": action.action_id,
            "run_id": run_id,
            "idempotency_key": action.idempotency_key,
            "initiator": BUYER,
            "approver": None,
            "executor": BUYER,
            "proposal_digest": action.proposal_digest,
            "target_doctype": None,
            "target_name": None,
            "verified_fields": {},
            "response_category": "ERP_VALIDATION_ERROR",
            "failure_category": "VALIDATION_ERROR",
            "final_state": "FAILED",
            "started_at": _future(),
            "completed_at": _future(),
            "correlation_id": str(uuid4()),
            "reconciliation_evidence": None,
        }
        receipt = create_execution_receipt(receipt_input)
        self.assertEqual(persist_execution_receipt(receipt)["final_state"], "FAILED")

    def test_stale_or_illegal_transition_and_direct_mutation_fail_closed(self) -> None:
        run_id = self._run()
        action = build_proposed_action(_action(run_id))
        persist_proposed_action(action)
        with self.assertRaises(GatewayFault):
            transition_action_state(
                action.action_id,
                "APPROVED",
                expected_version=1,
                reason="skip approval",
                correlation_id=str(uuid4()),
            )
        with self.assertRaises(GatewayFault):
            transition_action_state(
                action.action_id,
                "AWAITING_APPROVAL",
                expected_version=0,
                reason="stale version",
                correlation_id=str(uuid4()),
            )
        doc = frappe.get_doc("Synora Proposed Action", action.action_id)
        doc.summary = "tampered"
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            doc.delete(ignore_permissions=True)

    def test_success_receipt_cannot_be_forged_by_service_without_verified_flag(self) -> None:
        run_id = self._run()
        action = build_proposed_action(_action(run_id))
        persist_proposed_action(action)
        receipt = create_execution_receipt(
            {
                "receipt_id": str(uuid4()),
                "action_id": action.action_id,
                "run_id": run_id,
                "idempotency_key": action.idempotency_key,
                "initiator": BUYER,
                "approver": None,
                "executor": BUYER,
                "proposal_digest": action.proposal_digest,
                "target_doctype": "Material Request",
                "target_name": "MAT-REQ-FAKE",
                "verified_fields": {"docstatus": 0},
                "response_category": "ERP_SUCCESS",
                "failure_category": None,
                "final_state": "SUCCEEDED",
                "started_at": _future(),
                "completed_at": _future(),
                "correlation_id": str(uuid4()),
                "reconciliation_evidence": None,
            }
        )
        with self.assertRaises(GatewayFault):
            persist_execution_receipt(receipt)

    def test_regular_user_cannot_create_governance_record_from_doc_api(self) -> None:
        run_id = self._run()
        action = build_proposed_action(_action(run_id))
        values = {
            "doctype": "Synora Proposed Action",
            "name": action.action_id,
            "schema_version": action.schema_version,
            "action_type": action.action_type,
            "run": action.run_id,
            "action_id": action.action_id,
            "initiator": action.initiator,
            "payload_json": "{}",
            "evidence_refs_json": "[]",
            "calculation_refs_json": "[]",
            "risk_class": action.risk_class,
            "approval_class": action.approval_class,
            "snapshot_ref": action.snapshot_ref,
            "idempotency_key": action.idempotency_key,
            "expires_at": action.expires_at,
            "revalidation_rule": action.revalidation_rule,
            "proposal_digest": action.proposal_digest,
            "summary": action.summary,
            "state": "DRAFT",
            "state_version": 1,
            "state_reason": "direct",
            "state_changed_at": action.expires_at,
            "state_changed_by": BUYER,
            "correlation_id": action.correlation_id,
        }
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc(values).insert()
