"""Frappe integration tests for the governed Purchase Order Draft writer."""

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
    execute_purchase_order,
    get_run,
    issue_run,
    reconcile_purchase_order,
)
from synora_agentic_erp.governance.contracts import build_proposed_action
from synora_agentic_erp.governance.execution_contracts import purchase_order_values

BUYER = "synora-p1-buyer@dev.localhost"
VIEWER = "synora-p1-viewer@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
SUPPLIER = "SYNORA-P1-Supplier-1"
PRICE_LIST = "SYNORA-P1 Buying CNY"
ITEM_GROUP = "SYNORA-P1 Items"
STOCK_UOM = "Unit"


def _future() -> str:
    return "2030-01-01T00:00:00+00:00"


class TestGovernedPurchaseOrderExecution(FrappeTestCase):  # type: ignore[misc]
    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        super().tearDown()

    def _new_item(self, *, with_price: bool = True, price_rate: str = "100") -> str:
        item_code = f"SYNORA-P6-PO-{uuid4().hex[:12]}"
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
        if with_price:
            frappe.get_doc(
                {
                    "doctype": "Item Price",
                    "item_code": item_code,
                    "price_list": PRICE_LIST,
                    "price_list_rate": price_rate,
                    "currency": "CNY",
                    "uom": STOCK_UOM,
                    "supplier": SUPPLIER,
                    "buying": 1,
                    "selling": 0,
                    "valid_from": "2026-01-01",
                }
            ).insert(ignore_permissions=True)
        frappe.db.commit()
        return item_code

    def _proposal(self, item_code: str) -> dict[str, Any]:
        frappe.set_user(BUYER)
        issued = issue_run(
            COMPANY,
            f"ensure supplier order for {item_code}",
            warehouse=WAREHOUSE,
            correlation_id=str(uuid4()),
        )
        self.assertTrue(issued["ok"], issued)
        run = issued["run"]
        analyzed = analyze_run(str(run["run_id"]), str(issued["correlation_id"]))
        self.assertTrue(analyzed["ok"], analyzed)
        return {
            "schema_version": "1",
            "action_type": "CREATE_PO_DRAFT",
            "run_id": str(run["run_id"]),
            "action_id": str(uuid4()),
            "initiator": BUYER,
            "payload": {
                "company": COMPANY,
                "supplier": SUPPLIER,
                "transaction_date": "2026-08-27",
                "schedule_date": "2026-09-01",
                "currency": "CNY",
                "buying_price_list": PRICE_LIST,
                "items": [
                    {
                        "item_code": item_code,
                        "qty": "2",
                        "uom": STOCK_UOM,
                        "rate": "100",
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
            "idempotency_key": f"p6-po-{uuid4().hex}",
            "expires_at": _future(),
            "revalidation_rule": "FULL_PRE_EXECUTE_RECHECK_V1",
            "summary": "Create one Purchase Order Draft",
            "correlation_id": str(uuid4()),
        }

    def _approved_action(self, item_code: str) -> tuple[dict[str, Any], dict[str, Any]]:
        proposal = self._proposal(item_code)
        reviewed = evaluate_proposal(proposal)
        self.assertTrue(reviewed["ok"], reviewed)
        action = reviewed["action"]
        approved = decide_action(
            str(proposal["action_id"]),
            "ALLOW",
            str(action["proposal_digest"]),
            "confirm the reviewed purchase order draft",
            str(uuid4()),
        )
        self.assertTrue(approved["ok"], approved)
        self.assertEqual(approved["action"]["state"], "APPROVED")
        return proposal, approved["action"]

    def _execute(self, proposal: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        frappe.set_user(BUYER)
        return cast(
            dict[str, Any],
            execute_purchase_order(
                proposal["action_id"],
                action["proposal_digest"],
                proposal["idempotency_key"],
                str(uuid4()),
            ),
        )

    def _uncertain_action(self, item_code: str) -> tuple[dict[str, Any], dict[str, Any]]:
        proposal, action = self._approved_action(item_code)
        with patch(
            "synora_agentic_erp.governance.purchase_order_execution.verify_purchase_order_read_back",
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
        target = frappe.get_doc(purchase_order_values(action))
        target.set_missing_values()
        target.insert(ignore_permissions=True)
        frappe.db.commit()
        return str(target.name)

    def _reconcile(self, proposal: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        frappe.set_user(BUYER)
        return cast(
            dict[str, Any],
            reconcile_purchase_order(
                proposal["action_id"],
                action["proposal_digest"],
                proposal["idempotency_key"],
                str(uuid4()),
            ),
        )

    def test_success_creates_one_draft_and_closes_governance_facts(self) -> None:
        item_code = self._new_item()
        proposal, action = self._approved_action(item_code)
        before = frappe.db.count("Purchase Order")

        response = self._execute(proposal, action)

        self.assertTrue(response["ok"], response)
        self.assertEqual(frappe.db.count("Purchase Order"), before + 1)
        target_name = str(response["target"]["name"])
        target = frappe.get_doc("Purchase Order", target_name)
        self.assertEqual(target.docstatus, 0)
        self.assertEqual(target.supplier, SUPPLIER)
        self.assertEqual(target.company, COMPANY)
        self.assertEqual(target.currency, "CNY")
        self.assertEqual(target.buying_price_list, PRICE_LIST)
        self.assertEqual(str(target.conversion_rate), "1.0")
        self.assertEqual(str(target.schedule_date), "2026-09-01")
        self.assertEqual(len(target.items), 1)
        self.assertEqual(target.items[0].item_code, item_code)
        self.assertEqual(target.items[0].warehouse, WAREHOUSE)
        self.assertEqual(target.items[0].uom, STOCK_UOM)
        self.assertEqual(str(target.items[0].qty), "2.0")
        self.assertEqual(str(target.items[0].rate), "100.0")
        self.assertEqual(str(target.items[0].amount), "200.0")

        frappe.set_user("Administrator")
        receipt = frappe.get_doc("Synora Execution Receipt", response["receipt"]["receipt_id"])
        reservation = frappe.get_doc(
            "Synora Execution Reservation", response["reservation"]["reservation_id"]
        )
        self.assertEqual(receipt.final_state, "SUCCEEDED")
        self.assertEqual(receipt.target_doctype, "Purchase Order")
        self.assertEqual(receipt.target_name, target_name)
        self.assertEqual(json.loads(receipt.verified_fields_json)["item_0.amount"], "200")
        self.assertEqual(reservation.target_doctype, "Purchase Order")
        self.assertEqual(reservation.status, "SUCCEEDED")
        self.assertEqual(
            frappe.db.get_value("Synora Proposed Action", proposal["action_id"], "state"),
            "EXECUTED",
        )
        self.assertEqual(
            frappe.db.get_value("Synora Agent Run", proposal["run_id"], "run_state"),
            "SUCCEEDED",
        )
        self.assertTrue(
            frappe.db.exists(
                "Synora Gateway Audit",
                {"run": proposal["run_id"], "tool_name": "governed.purchase_order.create"},
            )
        )

    def test_same_key_replay_does_not_call_purchase_order_controller(self) -> None:
        proposal, action = self._approved_action(self._new_item())
        first = self._execute(proposal, action)
        self.assertTrue(first["ok"], first)
        original_insert = Document.insert

        def reject_second_purchase_order(
            self: Document, *args: object, **kwargs: object
        ) -> Document:
            if self.doctype == "Purchase Order":
                raise AssertionError("replay must not call the ERP controller")
            return original_insert(self, *args, **kwargs)

        with patch.object(Document, "insert", new=reject_second_purchase_order):
            second = self._execute(proposal, action)
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["target"]["name"], first["target"]["name"])
        self.assertEqual(second["receipt"]["receipt_id"], first["receipt"]["receipt_id"])

    def test_viewer_cannot_execute_an_approved_buyer_action(self) -> None:
        proposal, action = self._approved_action(self._new_item())
        before = frappe.db.count("Purchase Order")
        frappe.set_user(VIEWER)
        response = execute_purchase_order(
            proposal["action_id"],
            action["proposal_digest"],
            proposal["idempotency_key"],
            str(uuid4()),
        )
        self.assertFalse(response["ok"])
        self.assertIn(response["error"]["code"], {"CONFLICT", "PERMISSION_DENIED"})
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.count("Purchase Order"), before)
        self.assertEqual(
            frappe.db.count("Synora Execution Reservation", {"action": proposal["action_id"]}),
            0,
        )

    def test_controller_validation_failure_is_receipted_without_a_purchase_order(self) -> None:
        proposal, action = self._approved_action(self._new_item())
        before = frappe.db.count("Purchase Order")
        original_insert = Document.insert

        def reject_purchase_order(self: Document, *args: object, **kwargs: object) -> Document:
            if self.doctype == "Purchase Order":
                raise frappe.ValidationError("forced controller validation failure")
            return original_insert(self, *args, **kwargs)

        with patch.object(Document, "insert", new=reject_purchase_order):
            response = self._execute(proposal, action)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "ERP_VALIDATION_ERROR")
        frappe.set_user("Administrator")
        reservation = frappe.get_last_doc(
            "Synora Execution Reservation", filters={"action": proposal["action_id"]}
        )
        receipt = frappe.get_doc("Synora Execution Receipt", reservation.receipt)
        self.assertEqual(frappe.db.count("Purchase Order"), before)
        self.assertEqual(reservation.status, "FAILED")
        self.assertEqual(receipt.final_state, "FAILED")
        self.assertEqual(receipt.response_category, "ERP_VALIDATION_ERROR")
        self.assertEqual(
            frappe.db.get_value("Synora Proposed Action", proposal["action_id"], "state"),
            "EXPIRED",
        )
        self.assertEqual(
            frappe.db.get_value("Synora Agent Run", proposal["run_id"], "run_state"),
            "FAILED",
        )

    def test_read_back_mismatch_rolls_back_purchase_order(self) -> None:
        proposal, action = self._approved_action(self._new_item())
        before = frappe.db.count("Purchase Order")
        with patch(
            "synora_agentic_erp.governance.purchase_order_execution.verify_purchase_order_read_back",
            side_effect=ValueError("forced critical-field drift"),
        ):
            response = self._execute(proposal, action)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "UNCERTAIN_RESULT")
        frappe.set_user("Administrator")
        reservation = frappe.get_last_doc(
            "Synora Execution Reservation", filters={"action": proposal["action_id"]}
        )
        receipt = frappe.get_doc("Synora Execution Receipt", reservation.receipt)
        self.assertEqual(frappe.db.count("Purchase Order"), before)
        self.assertEqual(reservation.status, "RECONCILIATION_REQUIRED")
        self.assertEqual(receipt.final_state, "RECONCILIATION_REQUIRED")

    def test_uncertain_active_lease_exposes_no_retry_or_candidate_scan(self) -> None:
        proposal, action = self._uncertain_action(self._new_item())
        frappe.set_user(BUYER)
        with patch(
            "synora_agentic_erp.governance.purchase_order_execution._reconciliation_candidates",
            side_effect=AssertionError("active lease must not inspect ERP candidates"),
        ):
            response = self._reconcile(proposal, action)
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["result_status"], "RECONCILIATION_REQUIRED")
        self.assertFalse(response["can_retry"])

    def test_expired_lease_with_one_matching_draft_reconciles_without_writer_retry(self) -> None:
        proposal, action = self._uncertain_action(self._new_item())
        target_name = self._create_fixture_draft(proposal)
        original_insert = Document.insert

        def reject_reconciliation_writer(
            self: Document, *args: object, **kwargs: object
        ) -> Document:
            if self.doctype == "Purchase Order":
                raise AssertionError("reconciliation must never call the ERP writer")
            return original_insert(self, *args, **kwargs)

        with patch.object(Document, "insert", new=reject_reconciliation_writer):
            with patch(
                "synora_agentic_erp.governance.purchase_order_execution._lease_expired",
                return_value=True,
            ):
                response = self._reconcile(proposal, action)
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["result_status"], "RECONCILED_SUCCESS")
        self.assertFalse(response["can_retry"])
        self.assertEqual(response["target"]["name"], target_name)
        self.assertEqual(response["receipt"]["final_state"], "RECONCILED_SUCCESS")
        frappe.set_user("Administrator")
        self.assertEqual(
            frappe.db.get_value(
                "Synora Execution Reservation",
                response["reservation"]["reservation_id"],
                "status",
            ),
            "RECONCILED_SUCCESS",
        )

    def test_expired_lease_without_matching_draft_is_terminal_failure(self) -> None:
        proposal, action = self._uncertain_action(self._new_item())
        before = frappe.db.count("Purchase Order")
        with patch(
            "synora_agentic_erp.governance.purchase_order_execution._lease_expired",
            return_value=True,
        ):
            response = self._reconcile(proposal, action)
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["result_status"], "RECONCILED_FAILURE")
        self.assertFalse(response["can_retry"])
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.count("Purchase Order"), before)
        self.assertEqual(response["receipt"]["final_state"], "RECONCILED_FAILURE")
        self.assertEqual(
            frappe.db.get_value("Synora Proposed Action", proposal["action_id"], "state"),
            "EXPIRED",
        )

    def test_multiple_matching_drafts_become_manual_intervention(self) -> None:
        proposal, action = self._uncertain_action(self._new_item())
        first = self._create_fixture_draft(proposal)
        second = self._create_fixture_draft(proposal)
        self.assertNotEqual(first, second)
        with patch(
            "synora_agentic_erp.governance.purchase_order_execution._lease_expired",
            return_value=True,
        ):
            response = self._reconcile(proposal, action)
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["result_status"], "MANUAL_INTERVENTION")
        self.assertFalse(response["can_retry"])
        self.assertEqual(response["reconciliation"]["candidate_count"], 2)
        self.assertEqual(response["receipt"]["final_state"], "MANUAL_INTERVENTION")

    def test_response_loss_after_commit_replays_without_second_controller_insert(self) -> None:
        proposal, action = self._approved_action(self._new_item())
        with patch(
            "synora_agentic_erp.governance.purchase_order_execution._success_response",
            side_effect=RuntimeError("simulated response delivery loss"),
        ):
            first = self._execute(proposal, action)
        self.assertFalse(first["ok"])
        self.assertEqual(first["error"]["code"], "UNCERTAIN_RESULT")
        frappe.set_user("Administrator")
        reservation = frappe.get_last_doc(
            "Synora Execution Reservation", filters={"action": proposal["action_id"]}
        )
        self.assertEqual(reservation.status, "SUCCEEDED")
        self.assertEqual(
            frappe.db.get_value("Synora Execution Receipt", reservation.receipt, "final_state"),
            "SUCCEEDED",
        )
        original_insert = Document.insert

        def reject_replay_writer(self: Document, *args: object, **kwargs: object) -> Document:
            if self.doctype == "Purchase Order":
                raise AssertionError("response replay must not call the ERP controller")
            return original_insert(self, *args, **kwargs)

        with patch.object(Document, "insert", new=reject_replay_writer):
            second = self._execute(proposal, action)
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["target"]["name"], reservation.target_name)

    def test_policy_rejects_supplier_visibility_failure_before_approval(self) -> None:
        proposal = self._proposal(self._new_item())
        with patch(
            "synora_agentic_erp.governance.policy._visible_supplier",
            return_value=None,
        ):
            reviewed = evaluate_proposal(proposal)
        self.assertTrue(reviewed["ok"], reviewed)
        self.assertEqual(reviewed["policy"]["outcome"], "REJECT")

    def test_policy_rejects_rate_that_is_not_from_item_price(self) -> None:
        item_code = self._new_item()
        proposal = self._proposal(item_code)
        proposal["payload"]["items"][0]["rate"] = "0.01"
        before = frappe.db.count("Purchase Order")

        reviewed = evaluate_proposal(proposal)

        self.assertTrue(reviewed["ok"], reviewed)
        self.assertEqual(reviewed["action"]["state"], "POLICY_REJECTED")
        self.assertEqual(reviewed["policy"]["checks"]["deterministic"], "FAIL")
        self.assertIn("authoritative buying price", reviewed["policy"]["reason"])
        self.assertEqual(frappe.db.count("Purchase Order"), before)

    def test_policy_rejects_missing_item_price_before_approval(self) -> None:
        item_code = self._new_item(with_price=False)
        proposal = self._proposal(item_code)
        before = frappe.db.count("Purchase Order")

        reviewed = evaluate_proposal(proposal)

        self.assertTrue(reviewed["ok"], reviewed)
        self.assertEqual(reviewed["action"]["state"], "POLICY_REJECTED")
        self.assertEqual(reviewed["policy"]["checks"]["deterministic"], "FAIL")
        self.assertEqual(frappe.db.count("Purchase Order"), before)
        self.assertEqual(reviewed["action"]["state"], "POLICY_REJECTED")

    def test_run_details_include_governance_proposal_policy_and_approval(self) -> None:
        proposal, _action = self._approved_action(self._new_item())
        frappe.set_user(BUYER)
        response = get_run(proposal["run_id"])
        self.assertTrue(response["ok"], response)
        self.assertEqual(len(response["governance"]), 1)
        entry = response["governance"][0]
        self.assertEqual(entry["action"]["action_id"], proposal["action_id"])
        self.assertEqual(entry["action"]["action_type"], "CREATE_PO_DRAFT")
        self.assertEqual(entry["policy"]["outcome"], "ALLOW")
        self.assertEqual(entry["approval"]["decision"], "ALLOW")
        self.assertIsNone(entry["reservation"])
        self.assertIsNone(entry["receipt"])

    def test_endpoint_is_identifier_only_and_writer_never_submits_or_uses_generic_payload(
        self,
    ) -> None:
        parameters = set(signature(execute_purchase_order).parameters)
        self.assertEqual(
            parameters,
            {"action_id", "expected_proposal_digest", "idempotency_key", "correlation_id"},
        )
        source = (
            Path(__file__).parents[1] / "governance" / "purchase_order_execution.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("target.submit(", source)
        self.assertNotIn("target.insert(ignore_permissions", source)
        self.assertNotIn('frappe.get_doc({"doctype": payload["doctype"]})', source)
