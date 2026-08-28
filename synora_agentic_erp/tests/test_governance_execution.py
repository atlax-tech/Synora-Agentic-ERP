"""Frappe integration tests for the Phase 6 Material Request Draft writer."""

import json
from inspect import signature
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.model.document import Document
from frappe.tests.utils import FrappeTestCase

from synora_agentic_erp.api import (
    analyze_run,
    decide_action,
    evaluate_proposal,
    execute_material_request,
    get_run,
    issue_run,
    reconcile_material_request,
)
from synora_agentic_erp.governance.contracts import build_proposed_action
from synora_agentic_erp.governance.execution_contracts import (
    ReadBackMismatch,
    material_request_values,
)

BUYER = "synora-p1-buyer@dev.localhost"
VIEWER = "synora-p1-viewer@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
ITEM_GROUP = "SYNORA-P1 Items"
STOCK_UOM = "Unit"


def _future() -> str:
    return "2030-01-01T00:00:00+00:00"


class TestGovernedMaterialRequestExecution(FrappeTestCase):  # type: ignore[misc]
    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        super().tearDown()

    def _new_item(self) -> str:
        """Create isolated test master data before switching to the Buyer role."""
        item_code = f"SYNORA-P6-MR-{uuid4().hex[:12]}"
        frappe.set_user("Administrator")
        frappe.get_doc(
            {
                "doctype": "Item",
                "item_code": item_code,
                "item_name": item_code,
                "item_group": ITEM_GROUP,
                "stock_uom": STOCK_UOM,
                "is_stock_item": 1,
            }
        ).insert(ignore_permissions=True)
        frappe.db.commit()
        return item_code

    def _approved_action(self, item_code: str) -> tuple[dict[str, Any], dict[str, Any]]:
        frappe.set_user(BUYER)
        issued = issue_run(
            COMPANY,
            f"ensure stock for {item_code}",
            warehouse=WAREHOUSE,
            correlation_id=str(uuid4()),
        )
        self.assertTrue(issued["ok"], issued)
        run = issued["run"]
        analyzed = analyze_run(str(run["run_id"]), str(issued["correlation_id"]))
        self.assertTrue(analyzed["ok"], analyzed)
        proposal: dict[str, Any] = {
            "schema_version": "1",
            "action_type": "CREATE_MR_DRAFT",
            "run_id": str(run["run_id"]),
            "action_id": str(uuid4()),
            "initiator": BUYER,
            "payload": {
                "company": COMPANY,
                "transaction_date": "2026-08-27",
                "material_request_type": "Purchase",
                "items": [
                    {
                        "item_code": item_code,
                        "qty": "2",
                        "uom": STOCK_UOM,
                        "schedule_date": "2026-09-01",
                        "warehouse": WAREHOUSE,
                    }
                ],
            },
            "evidence_refs": [f"observation:{item_code}"],
            "calculation_refs": [f"calculation:{item_code}"],
            "risk_class": "MEDIUM",
            "approval_class": "INITIATOR_CONFIRMATION",
            "snapshot_ref": f"snapshot:{uuid4()}",
            "idempotency_key": f"p6-mr-{uuid4().hex}",
            "expires_at": _future(),
            "revalidation_rule": "FULL_PRE_EXECUTE_RECHECK_V1",
            "summary": "Create one Material Request Draft",
            "correlation_id": str(uuid4()),
        }
        reviewed = evaluate_proposal(proposal)
        self.assertTrue(reviewed["ok"], reviewed)
        action = reviewed["action"]
        approved = decide_action(
            str(proposal["action_id"]),
            "ALLOW",
            str(action["proposal_digest"]),
            "confirm the reviewed draft",
            str(uuid4()),
        )
        self.assertTrue(approved["ok"], approved)
        self.assertEqual(approved["action"]["state"], "APPROVED")
        return proposal, approved["action"]

    def _execute(self, proposal: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        frappe.set_user(BUYER)
        response = execute_material_request(
            proposal["action_id"],
            action["proposal_digest"],
            proposal["idempotency_key"],
            str(uuid4()),
        )
        return cast(dict[str, Any], response)

    def _uncertain_action(self, item_code: str) -> tuple[dict[str, Any], dict[str, Any]]:
        proposal, action = self._approved_action(item_code)
        with patch(
            "synora_agentic_erp.governance.execution.verify_material_request_read_back",
            side_effect=RuntimeError("simulated lost ERP acknowledgement"),
        ):
            response = self._execute(proposal, action)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "UNCERTAIN_RESULT")
        frappe.set_user("Administrator")
        reservation = frappe.get_last_doc(
            "Synora Execution Reservation", filters={"action": proposal["action_id"]}
        )
        self.assertEqual(reservation.status, "RECONCILIATION_REQUIRED")
        self.assertEqual(reservation.failure_category, "UNEXPECTED_EXECUTION_ERROR")
        return proposal, action

    def _create_fixture_draft(self, proposal: dict[str, Any]) -> str:
        frappe.set_user("Administrator")
        action = build_proposed_action(proposal)
        target = frappe.get_doc(material_request_values(action)).insert(ignore_permissions=True)
        frappe.db.commit()
        return str(target.name)

    def test_success_uses_controller_reads_back_and_closes_all_governance_facts(self) -> None:
        item_code = self._new_item()
        proposal, action = self._approved_action(item_code)
        before = frappe.db.count("Material Request")

        response = self._execute(proposal, action)

        self.assertTrue(response["ok"], response)
        self.assertEqual(frappe.db.count("Material Request"), before + 1)
        target_name = str(response["target"]["name"])
        target = frappe.get_doc("Material Request", target_name)
        self.assertEqual(target.docstatus, 0)
        self.assertEqual(target.company, COMPANY)
        self.assertEqual(target.material_request_type, "Purchase")
        self.assertEqual(len(target.items), 1)
        self.assertEqual(target.items[0].item_code, item_code)
        self.assertEqual(target.items[0].warehouse, WAREHOUSE)
        self.assertEqual(str(target.items[0].qty), "2.0")

        frappe.set_user("Administrator")
        receipt = frappe.get_doc("Synora Execution Receipt", response["receipt"]["receipt_id"])
        reservation = frappe.get_doc(
            "Synora Execution Reservation", response["reservation"]["reservation_id"]
        )
        stored_action = frappe.get_doc("Synora Proposed Action", proposal["action_id"])
        run = frappe.get_doc("Synora Agent Run", proposal["run_id"])
        self.assertEqual(receipt.final_state, "SUCCEEDED")
        self.assertEqual(receipt.response_category, "ERP_SUCCESS")
        self.assertEqual(receipt.target_name, target_name)
        self.assertEqual(json.loads(receipt.verified_fields_json)["docstatus"], 0)
        self.assertEqual(reservation.status, "SUCCEEDED")
        self.assertEqual(reservation.target_name, target_name)
        self.assertEqual(stored_action.state, "EXECUTED")
        self.assertEqual(run.run_state, "SUCCEEDED")
        self.assertEqual(run.status, "REVOKED")
        audits = frappe.get_all(
            "Synora Gateway Audit",
            filters={"run": proposal["run_id"], "tool_name": "governed.material_request.create"},
            fields=["outcome", "error_code"],
        )
        self.assertEqual(audits[-1].outcome, "SUCCEEDED")
        self.assertIsNone(audits[-1].error_code)

    def test_same_key_replay_returns_verified_result_without_second_controller_insert(self) -> None:
        item_code = self._new_item()
        proposal, action = self._approved_action(item_code)
        first = self._execute(proposal, action)
        self.assertTrue(first["ok"], first)

        original_insert = Document.insert

        def reject_second_material_request(
            self: Document, *args: object, **kwargs: object
        ) -> Document:
            if self.doctype == "Material Request":
                raise AssertionError("replay must not call the ERP controller")
            return original_insert(self, *args, **kwargs)

        with patch.object(Document, "insert", new=reject_second_material_request):
            second = self._execute(proposal, action)

        self.assertTrue(second["ok"], second)
        self.assertEqual(second["target"]["name"], first["target"]["name"])
        self.assertEqual(second["receipt"]["receipt_id"], first["receipt"]["receipt_id"])
        frappe.set_user("Administrator")
        self.assertEqual(
            frappe.db.count("Material Request", {"name": first["target"]["name"]}),
            1,
        )

    def test_same_key_replay_read_back_drift_returns_typed_uncertain_result(self) -> None:
        item_code = self._new_item()
        proposal, action = self._approved_action(item_code)
        first = self._execute(proposal, action)
        self.assertTrue(first["ok"], first)
        before = frappe.db.count("Material Request")

        with patch(
            "synora_agentic_erp.governance.execution.verify_material_request_read_back",
            side_effect=ReadBackMismatch("forced replay drift"),
        ):
            second = self._execute(proposal, action)

        self.assertFalse(second["ok"])
        self.assertEqual(second["error"]["code"], "UNCERTAIN_RESULT")
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.count("Material Request"), before)

    def test_finalized_reconcile_rechecks_current_row_scope_before_returning_receipt(self) -> None:
        item_code = self._new_item()
        proposal, action = self._approved_action(item_code)
        first = self._execute(proposal, action)
        self.assertTrue(first["ok"], first)
        target_name = str(first["target"]["name"])
        original_get_list = frappe.get_list

        def hide_target(doctype: str, *args: object, **kwargs: object) -> object:
            filters = kwargs.get("filters")
            if doctype == "Material Request" and isinstance(filters, dict):
                if filters.get("name") == target_name:
                    return []
            return original_get_list(doctype, *args, **kwargs)

        with patch("frappe.get_list", side_effect=hide_target):
            response = reconcile_material_request(
                proposal["action_id"],
                action["proposal_digest"],
                proposal["idempotency_key"],
                str(uuid4()),
            )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "PERMISSION_DENIED")
        self.assertNotIn("target_name", response)
        self.assertNotIn("receipt", response)

    def test_reconciled_success_rechecks_current_row_scope_before_returning_receipt(self) -> None:
        item_code = self._new_item()
        proposal, action = self._uncertain_action(item_code)
        target_name = self._create_fixture_draft(proposal)
        frappe.set_user(BUYER)
        with patch(
            "synora_agentic_erp.governance.execution._lease_expired",
            return_value=True,
        ):
            first = reconcile_material_request(
                proposal["action_id"],
                action["proposal_digest"],
                proposal["idempotency_key"],
                str(uuid4()),
            )
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["result_status"], "RECONCILED_SUCCESS")
        self.assertEqual(first["target"]["name"], target_name)
        original_get_list = frappe.get_list

        def hide_target(doctype: str, *args: object, **kwargs: object) -> object:
            filters = kwargs.get("filters")
            if doctype == "Material Request" and isinstance(filters, dict):
                if filters.get("name") == target_name:
                    return []
            return original_get_list(doctype, *args, **kwargs)

        with patch("frappe.get_list", side_effect=hide_target):
            response = reconcile_material_request(
                proposal["action_id"],
                action["proposal_digest"],
                proposal["idempotency_key"],
                str(uuid4()),
            )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "PERMISSION_DENIED")
        self.assertNotIn("target_name", response)
        self.assertNotIn("receipt", response)

    def test_run_details_rechecks_current_row_scope_before_returning_receipt(self) -> None:
        item_code = self._new_item()
        proposal, action = self._approved_action(item_code)
        first = self._execute(proposal, action)
        self.assertTrue(first["ok"], first)
        target_name = str(first["target"]["name"])

        frappe.set_user(BUYER)
        visible = get_run(proposal["run_id"])
        self.assertTrue(visible["ok"], visible)
        self.assertEqual(visible["governance"][0]["receipt"]["target_name"], target_name)

        original_get_list = frappe.get_list

        def hide_target(doctype: str, *args: object, **kwargs: object) -> object:
            filters = kwargs.get("filters")
            if doctype == "Material Request" and isinstance(filters, dict):
                if filters.get("name") == target_name:
                    return []
            return original_get_list(doctype, *args, **kwargs)

        with patch("frappe.get_list", side_effect=hide_target):
            hidden = get_run(proposal["run_id"])
        self.assertFalse(hidden["ok"])
        self.assertEqual(hidden["error"]["code"], "PERMISSION_DENIED")
        self.assertNotIn("governance", hidden)

    def test_digest_conflict_is_rejected_before_reservation_or_business_write(self) -> None:
        item_code = self._new_item()
        proposal, _action = self._approved_action(item_code)
        before_mr = frappe.db.count("Material Request")

        frappe.set_user(BUYER)
        response = execute_material_request(
            proposal["action_id"],
            "0" * 64,
            proposal["idempotency_key"],
            str(uuid4()),
        )

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "CONFLICT")
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.count("Material Request"), before_mr)
        self.assertEqual(
            frappe.db.count("Synora Execution Reservation", {"action": proposal["action_id"]}),
            0,
        )
        self.assertEqual(
            frappe.db.get_value("Synora Proposed Action", proposal["action_id"], "state"),
            "APPROVED",
        )

    def test_current_viewer_cannot_execute_an_approved_buyer_action(self) -> None:
        item_code = self._new_item()
        proposal, action = self._approved_action(item_code)
        before_mr = frappe.db.count("Material Request")

        frappe.set_user(VIEWER)
        response = execute_material_request(
            proposal["action_id"],
            action["proposal_digest"],
            proposal["idempotency_key"],
            str(uuid4()),
        )

        self.assertFalse(response["ok"])
        self.assertIn(response["error"]["code"], {"CONFLICT", "PERMISSION_DENIED"})
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.count("Material Request"), before_mr)
        self.assertEqual(
            frappe.db.count("Synora Execution Reservation", {"action": proposal["action_id"]}),
            0,
        )

    def test_controller_validation_failure_is_receipted_and_does_not_leave_a_material_request(
        self,
    ) -> None:
        item_code = self._new_item()
        proposal, action = self._approved_action(item_code)
        before_mr = frappe.db.count("Material Request")
        original_insert = Document.insert

        def reject_material_request(self: Document, *args: object, **kwargs: object) -> Document:
            if self.doctype == "Material Request":
                raise frappe.ValidationError("forced controller validation failure")
            return original_insert(self, *args, **kwargs)

        with patch.object(Document, "insert", new=reject_material_request):
            response = self._execute(proposal, action)

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "ERP_VALIDATION_ERROR")
        frappe.set_user("Administrator")
        reservation = frappe.get_last_doc(
            "Synora Execution Reservation", filters={"action": proposal["action_id"]}
        )
        receipt = frappe.get_doc("Synora Execution Receipt", reservation.receipt)
        run = frappe.get_doc("Synora Agent Run", proposal["run_id"])
        stored_action = frappe.get_doc("Synora Proposed Action", proposal["action_id"])
        self.assertEqual(frappe.db.count("Material Request"), before_mr)
        self.assertEqual(reservation.status, "FAILED")
        self.assertEqual(receipt.final_state, "FAILED")
        self.assertEqual(receipt.response_category, "ERP_VALIDATION_ERROR")
        self.assertEqual(stored_action.state, "EXPIRED")
        self.assertEqual(run.run_state, "FAILED")

    def test_read_back_mismatch_rolls_back_target_and_requires_failure_receipt(self) -> None:
        item_code = self._new_item()
        proposal, action = self._approved_action(item_code)
        before_mr = frappe.db.count("Material Request")

        with patch(
            "synora_agentic_erp.governance.execution.verify_material_request_read_back",
            side_effect=ReadBackMismatch("forced critical-field drift"),
        ):
            response = self._execute(proposal, action)

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "ERP_VALIDATION_ERROR")
        frappe.set_user("Administrator")
        reservation = frappe.get_last_doc(
            "Synora Execution Reservation", filters={"action": proposal["action_id"]}
        )
        receipt = frappe.get_doc("Synora Execution Receipt", reservation.receipt)
        self.assertEqual(frappe.db.count("Material Request"), before_mr)
        self.assertEqual(reservation.status, "FAILED")
        self.assertEqual(receipt.final_state, "FAILED")
        self.assertEqual(receipt.failure_category, "ReadBackMismatch")

    def test_uncertain_result_exposes_no_retry_and_active_lease_blocks_reconciliation(self) -> None:
        item_code = self._new_item()
        proposal, action = self._uncertain_action(item_code)

        frappe.set_user(BUYER)
        with patch(
            "synora_agentic_erp.governance.execution._reconciliation_candidates",
            side_effect=AssertionError("active lease must not inspect ERP candidates"),
        ):
            response = reconcile_material_request(
                proposal["action_id"],
                action["proposal_digest"],
                proposal["idempotency_key"],
                str(uuid4()),
            )

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["result_status"], "RECONCILIATION_REQUIRED")
        self.assertFalse(response["can_retry"])
        self.assertEqual(response["reservation"]["status"], "RECONCILIATION_REQUIRED")

    def test_expired_lease_with_one_matching_draft_reconciles_without_writer_retry(self) -> None:
        item_code = self._new_item()
        proposal, action = self._uncertain_action(item_code)
        target_name = self._create_fixture_draft(proposal)

        original_insert = Document.insert

        def reject_reconciliation_writer(
            self: Document, *args: object, **kwargs: object
        ) -> Document:
            if self.doctype == "Material Request":
                raise AssertionError("reconciliation must never call the ERP writer")
            return original_insert(self, *args, **kwargs)

        frappe.set_user(BUYER)
        with patch.object(Document, "insert", new=reject_reconciliation_writer):
            with patch(
                "synora_agentic_erp.governance.execution._lease_expired",
                return_value=True,
            ):
                response = reconcile_material_request(
                    proposal["action_id"],
                    action["proposal_digest"],
                    proposal["idempotency_key"],
                    str(uuid4()),
                )

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["result_status"], "RECONCILED_SUCCESS")
        self.assertFalse(response["can_retry"])
        self.assertEqual(response["receipt"]["final_state"], "RECONCILED_SUCCESS")
        self.assertEqual(response["target"]["name"], target_name)
        frappe.set_user("Administrator")
        self.assertEqual(
            frappe.db.get_value(
                "Synora Execution Reservation",
                response["reservation"]["reservation_id"],
                "status",
            ),
            "RECONCILED_SUCCESS",
        )
        self.assertEqual(
            frappe.db.get_value("Synora Proposed Action", proposal["action_id"], "state"),
            "EXECUTED",
        )
        self.assertEqual(
            frappe.db.get_value("Synora Agent Run", proposal["run_id"], "run_state"),
            "SUCCEEDED",
        )

    def test_expired_lease_without_matching_draft_reconciles_failure_and_never_retries(
        self,
    ) -> None:
        item_code = self._new_item()
        proposal, action = self._uncertain_action(item_code)
        before_mr = frappe.db.count("Material Request")

        frappe.set_user(BUYER)
        with patch(
            "synora_agentic_erp.governance.execution._lease_expired",
            return_value=True,
        ):
            response = reconcile_material_request(
                proposal["action_id"],
                action["proposal_digest"],
                proposal["idempotency_key"],
                str(uuid4()),
            )

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["result_status"], "RECONCILED_FAILURE")
        self.assertFalse(response["can_retry"])
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.count("Material Request"), before_mr)
        self.assertEqual(response["receipt"]["final_state"], "RECONCILED_FAILURE")
        self.assertEqual(
            frappe.db.get_value("Synora Proposed Action", proposal["action_id"], "state"),
            "EXPIRED",
        )
        self.assertEqual(
            frappe.db.get_value("Synora Agent Run", proposal["run_id"], "run_state"),
            "FAILED",
        )

    def test_multiple_matching_drafts_become_manual_intervention_without_guessing(self) -> None:
        item_code = self._new_item()
        proposal, action = self._uncertain_action(item_code)
        first = self._create_fixture_draft(proposal)
        second = self._create_fixture_draft(proposal)
        self.assertNotEqual(first, second)

        frappe.set_user(BUYER)
        with patch(
            "synora_agentic_erp.governance.execution._lease_expired",
            return_value=True,
        ):
            response = reconcile_material_request(
                proposal["action_id"],
                action["proposal_digest"],
                proposal["idempotency_key"],
                str(uuid4()),
            )

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["result_status"], "MANUAL_INTERVENTION")
        self.assertFalse(response["can_retry"])
        self.assertEqual(response["receipt"]["final_state"], "MANUAL_INTERVENTION")
        self.assertEqual(response["reconciliation"]["candidate_count"], 2)

    def test_response_loss_after_commit_is_replayed_without_second_controller_insert(self) -> None:
        item_code = self._new_item()
        proposal, action = self._approved_action(item_code)
        with patch(
            "synora_agentic_erp.governance.execution._success_response",
            side_effect=RuntimeError("simulated response delivery loss"),
        ):
            first = self._execute(proposal, action)

        self.assertFalse(first["ok"])
        self.assertEqual(first["error"]["code"], "UNCERTAIN_RESULT")
        frappe.set_user("Administrator")
        reservation = frappe.get_last_doc(
            "Synora Execution Reservation", filters={"action": proposal["action_id"]}
        )
        receipt = frappe.get_doc("Synora Execution Receipt", reservation.receipt)
        self.assertEqual(reservation.status, "SUCCEEDED")
        self.assertEqual(receipt.final_state, "SUCCEEDED")
        self.assertEqual(
            frappe.db.get_value("Synora Proposed Action", proposal["action_id"], "state"),
            "EXECUTED",
        )

        original_insert = Document.insert

        def reject_replay_writer(self: Document, *args: object, **kwargs: object) -> Document:
            if self.doctype == "Material Request":
                raise AssertionError("response replay must not call the ERP controller")
            return original_insert(self, *args, **kwargs)

        with patch.object(Document, "insert", new=reject_replay_writer):
            second = self._execute(proposal, action)
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["target"]["name"], reservation.target_name)

    def test_endpoint_accepts_only_server_bound_execution_identifiers(self) -> None:
        parameters = set(signature(execute_material_request).parameters)
        self.assertEqual(
            parameters,
            {"action_id", "expected_proposal_digest", "idempotency_key", "correlation_id"},
        )
        source = (Path(__file__).parents[1] / "governance" / "execution.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("target.submit(", source)
        self.assertIn("tabSynora Execution Reservation", source)
        self.assertNotIn("target.insert(ignore_permissions", source)
