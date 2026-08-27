"""Phase 6 policy, approval, and pre-execute re-check integration tests."""

from datetime import UTC, datetime
from inspect import signature
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests.utils import FrappeTestCase

from synora_agentic_erp.api import decide_action, evaluate_proposal, issue_run
from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.governance.policy import GateResult, pre_execute_recheck

BUYER = "synora-p1-buyer@dev.localhost"
VIEWER = "synora-p1-viewer@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
ITEM = "Consulting"


def _future() -> str:
    return datetime(2030, 1, 1, tzinfo=UTC).isoformat()


def _action(
    run_id: str,
    *,
    action_id: str | None = None,
    approval_class: str = "INITIATOR_CONFIRMATION",
) -> dict[str, object]:
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
                    "item_code": ITEM,
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
        "approval_class": approval_class,
        "snapshot_ref": "snapshot:phase6-step003",
        "idempotency_key": f"p6-policy-{uuid4().hex}",
        "expires_at": _future(),
        "revalidation_rule": "FULL_PRE_EXECUTE_RECHECK_V1",
        "summary": "Create a material request draft",
        "correlation_id": str(uuid4()),
    }


class TestGovernancePolicy(FrappeTestCase):
    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        super().tearDown()

    def _run(self) -> str:
        frappe.set_user(BUYER)
        response = issue_run(
            COMPANY,
            "ensure stock for SYNORA-P1-Item-1001",
            warehouse=WAREHOUSE,
            correlation_id=str(uuid4()),
        )
        self.assertTrue(response["ok"])
        return str(response["run"]["run_id"])

    def _evaluate(self) -> tuple[str, dict[str, object]]:
        run_id = self._run()
        proposal = _action(run_id)
        frappe.set_user(BUYER)
        response = evaluate_proposal(proposal)
        self.assertTrue(response["ok"])
        return str(proposal["action_id"]), response

    def test_buyer_can_review_and_approve_without_business_document_write(self) -> None:
        before_mr = frappe.db.count("Material Request")
        before_po = frappe.db.count("Purchase Order")
        action_id, reviewed = self._evaluate()
        self.assertEqual(reviewed["action"]["state"], "AWAITING_APPROVAL")
        self.assertEqual(reviewed["policy"]["outcome"], "ALLOW")
        digest = str(reviewed["action"]["proposal_digest"])

        approved = decide_action(
            action_id,
            "ALLOW",
            digest,
            "I confirm this draft proposal",
            str(uuid4()),
        )
        self.assertTrue(approved["ok"])
        self.assertEqual(approved["action"]["state"], "APPROVED")
        context = pre_execute_recheck(action_id, digest, reviewed["action"]["idempotency_key"])
        self.assertEqual(context.target_doctype, "Material Request")
        self.assertEqual(context.proposal_digest, digest)
        self.assertEqual(frappe.db.count("Material Request"), before_mr)
        self.assertEqual(frappe.db.count("Purchase Order"), before_po)

    def test_viewer_cannot_create_or_review_a_buyer_proposal(self) -> None:
        before_actions = frappe.db.count("Synora Proposed Action")
        run_id = self._run()
        frappe.set_user(VIEWER)
        response = evaluate_proposal(_action(run_id))
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "PERMISSION_DENIED")
        self.assertEqual(frappe.db.count("Synora Proposed Action"), before_actions)

    def test_scope_failure_short_circuits_sensitive_policy_checks(self) -> None:
        run_id = self._run()
        proposal = _action(run_id)
        proposal_payload = dict(proposal["payload"])
        proposal_payload["company"] = "SYNORA-P1 Other Company"
        proposal["payload"] = proposal_payload
        frappe.set_user(BUYER)
        response = evaluate_proposal(proposal)
        self.assertTrue(response["ok"])
        self.assertEqual(response["action"]["state"], "POLICY_REJECTED")
        self.assertEqual(response["policy"]["checks"]["scope"], "FAIL")
        self.assertEqual(response["policy"]["checks"]["permission"], "UNKNOWN")
        self.assertEqual(response["policy"]["checks"]["deterministic"], "UNKNOWN")
        self.assertEqual(response["policy"]["checks"]["workflow_policy"], "UNKNOWN")

    def test_active_workflow_is_stricter_and_fails_closed(self) -> None:
        with patch(
            "synora_agentic_erp.governance.policy._workflow_policy",
            return_value=GateResult("UNKNOWN", "active ERP Workflow requires a new mapping"),
        ):
            _, response = self._evaluate()
        self.assertEqual(response["policy"]["outcome"], "REJECT")
        self.assertEqual(response["policy"]["checks"]["workflow_policy"], "UNKNOWN")

    def test_digest_mismatch_and_duplicate_approval_are_conflicts(self) -> None:
        action_id, reviewed = self._evaluate()
        digest = str(reviewed["action"]["proposal_digest"])
        mismatch = decide_action(action_id, "ALLOW", "0" * 64, "wrong digest", str(uuid4()))
        self.assertFalse(mismatch["ok"])
        self.assertEqual(mismatch["error"]["code"], "CONFLICT")
        first = decide_action(action_id, "ALLOW", digest, "confirm", str(uuid4()))
        self.assertTrue(first["ok"])
        second = decide_action(action_id, "ALLOW", digest, "duplicate click", str(uuid4()))
        self.assertFalse(second["ok"])
        self.assertEqual(second["error"]["code"], "CONFLICT")
        stored = frappe.get_doc("Synora Proposed Action", action_id)
        self.assertEqual(stored.state, "APPROVED")
        self.assertEqual(
            frappe.db.count("Synora Approval Decision", {"action": action_id}),
            1,
        )

    def test_changes_requested_does_not_mutate_payload_or_approve(self) -> None:
        action_id, reviewed = self._evaluate()
        digest = str(reviewed["action"]["proposal_digest"])
        requested = decide_action(
            action_id, "CHANGES_REQUESTED", digest, "change quantity", str(uuid4())
        )
        self.assertTrue(requested["ok"])
        self.assertEqual(requested["action"]["state"], "AWAITING_APPROVAL")
        self.assertEqual(requested["action"]["proposal_digest"], digest)
        action = frappe.get_doc("Synora Proposed Action", action_id)
        self.assertEqual(action.state_version, 2)
        approved = decide_action(action_id, "ALLOW", digest, "updated confirmation", str(uuid4()))
        self.assertTrue(approved["ok"])
        self.assertEqual(approved["action"]["state"], "APPROVED")

    def test_current_draft_contract_rejects_independent_approval_class(self) -> None:
        run_id = self._run()
        proposal = _action(run_id, approval_class="INDEPENDENT_APPROVER")
        before_actions = frappe.db.count("Synora Proposed Action")
        frappe.set_user(BUYER)
        rejected = evaluate_proposal(proposal)
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["error"]["code"], "INVALID_INPUT")
        self.assertEqual(frappe.db.count("Synora Proposed Action"), before_actions)

    def test_expired_action_is_expired_by_owner_and_precheck_never_writes_erp(self) -> None:
        before_mr = frappe.db.count("Material Request")
        action_id, reviewed = self._evaluate()
        digest = str(reviewed["action"]["proposal_digest"])
        approved = decide_action(action_id, "ALLOW", digest, "confirm", str(uuid4()))
        self.assertTrue(approved["ok"])
        with patch(
            "synora_agentic_erp.governance.policy._expiry_passes",
            side_effect=[True, True, False],
        ):
            with self.assertRaises(GatewayFault) as captured:
                pre_execute_recheck(action_id, digest, reviewed["action"]["idempotency_key"])
        self.assertEqual(getattr(captured.exception, "code", None), "CONFLICT")
        self.assertEqual(
            frappe.db.get_value("Synora Proposed Action", action_id, "state"), "EXPIRED"
        )
        self.assertEqual(frappe.db.count("Material Request"), before_mr)

    def test_permission_revocation_and_duplicate_state_drift_fail_precheck(self) -> None:
        action_id, reviewed = self._evaluate()
        digest = str(reviewed["action"]["proposal_digest"])
        approved = decide_action(action_id, "ALLOW", digest, "confirm", str(uuid4()))
        self.assertTrue(approved["ok"])
        with patch("frappe.has_permission", return_value=False):
            with self.assertRaises(GatewayFault) as captured:
                pre_execute_recheck(action_id, digest, reviewed["action"]["idempotency_key"])
        self.assertEqual(getattr(captured.exception, "code", None), "CONFLICT")

        action_id, reviewed = self._evaluate()
        digest = str(reviewed["action"]["proposal_digest"])
        decide_action(action_id, "ALLOW", digest, "confirm", str(uuid4()))
        with patch(
            "synora_agentic_erp.governance.policy._open_duplicate",
            return_value=True,
        ):
            with self.assertRaises(GatewayFault) as captured:
                pre_execute_recheck(action_id, digest, reviewed["action"]["idempotency_key"])
        self.assertEqual(getattr(captured.exception, "code", None), "CONFLICT")

    def test_uom_lookup_is_scoped_to_current_actor(self) -> None:
        frappe.set_user("Administrator")
        issued = issue_run(
            COMPANY,
            "verify alternate UOM visibility",
            warehouse=WAREHOUSE,
            correlation_id=str(uuid4()),
        )
        self.assertTrue(issued["ok"], issued)
        run_id = str(issued["run"]["run_id"])
        proposal = _action(run_id)
        proposal["initiator"] = "Administrator"
        payload = dict(proposal["payload"])
        items = [dict(item) for item in payload["items"]]
        items[0]["uom"] = "Unit"
        payload["items"] = items
        proposal["payload"] = payload
        calls: list[object] = []
        original_get_list = frappe.get_list

        def track_uom_lookup(*args: object, **kwargs: object) -> object:
            if args and args[0] == "UOM":
                calls.append(kwargs.get("user"))
            return original_get_list(*args, **kwargs)

        with patch("frappe.get_list", side_effect=track_uom_lookup):
            reviewed = evaluate_proposal(proposal)
        self.assertTrue(reviewed["ok"], reviewed)
        self.assertEqual(calls, ["Administrator"])

    def test_approval_endpoint_has_no_client_actor_or_role_parameter(self) -> None:
        self.assertNotIn("actor", signature(decide_action).parameters)
        self.assertNotIn("role", signature(decide_action).parameters)

    def test_policy_module_has_no_business_document_writer(self) -> None:
        source = Path(__file__).parents[1] / "governance" / "policy.py"
        text = source.read_text(encoding="utf-8")
        self.assertNotIn(".insert(", text)
        self.assertNotIn(".save(", text)
        self.assertNotIn(".submit(", text)
