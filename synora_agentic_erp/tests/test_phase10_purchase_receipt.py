"""Real Frappe acceptance for governed partial Purchase Receipts."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests.utils import FrappeTestCase

from synora_agentic_erp.api import (
    analyze_run,
    decide_action,
    evaluate_proposal,
    execute_p2p_action,
    get_run,
    issue_run,
)
from synora_agentic_erp.governance import p2p_execution
from synora_agentic_erp.tests.phase10_test_helpers import cancel_p10_test_documents

BUYER = "synora-p1-buyer@dev.localhost"
RECEIVER = "synora-p1-receiver@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
SUPPLIER = "SYNORA-P1-Supplier-1"
PRICE_LIST = "SYNORA-P1 Buying CNY"
ITEM_GROUP = "SYNORA-P1 Items"
STOCK_UOM = "Unit"


class TestPhase10PurchaseReceipt(FrappeTestCase):  # type: ignore[misc]
    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        cancel_p10_test_documents()
        super().tearDown()

    def _submitted_po(self) -> tuple[str, str, str]:
        item_code = f"SYNORA-P10-PR-{uuid4().hex[:12]}"
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
        frappe.get_doc(
            {
                "doctype": "Item Price",
                "item_code": item_code,
                "price_list": PRICE_LIST,
                "price_list_rate": 10,
                "currency": "CNY",
                "uom": STOCK_UOM,
                "supplier": SUPPLIER,
                "buying": 1,
                "selling": 0,
                "valid_from": "2026-01-01",
            }
        ).insert(ignore_permissions=True)
        po = frappe.get_doc(
            {
                "doctype": "Purchase Order",
                "supplier": SUPPLIER,
                "company": COMPANY,
                "transaction_date": "2026-09-10",
                "schedule_date": "2026-09-20",
                "currency": "CNY",
                "buying_price_list": PRICE_LIST,
                "items": [
                    {
                        "item_code": item_code,
                        "qty": 2,
                        "uom": STOCK_UOM,
                        "conversion_factor": 1,
                        "rate": 10,
                        "warehouse": WAREHOUSE,
                        "schedule_date": "2026-09-20",
                    }
                ],
            }
        )
        po.set_missing_values()
        po.insert(ignore_permissions=True)
        po.submit()
        frappe.db.commit()
        return str(po.name), str(po.items[0].name), item_code

    def _proposal(
        self,
        action_type: str,
        source_doctype: str,
        source_name: str,
        *,
        source_row: str | None = None,
        item_code: str | None = None,
        quantity: int | None = None,
        uom: str = STOCK_UOM,
        warehouse: str = WAREHOUSE,
    ) -> dict[str, Any]:
        frappe.set_user(BUYER)
        issued = issue_run(
            COMPANY,
            f"{action_type} {source_name}",
            warehouse=WAREHOUSE,
            correlation_id=str(uuid4()),
        )
        self.assertTrue(issued["ok"], issued)
        run_id = str(issued["run"]["run_id"])
        analyzed = analyze_run(run_id, str(issued["correlation_id"]))
        self.assertTrue(analyzed["ok"], analyzed)
        payload: dict[str, Any] = {
            "company": COMPANY,
            "source_doctype": source_doctype,
            "source_name": source_name,
        }
        if action_type == "CREATE_PR_DRAFT":
            payload.update(
                {
                    "transaction_date": "2026-09-10",
                    "items": [
                        {
                            "source_row": source_row,
                            "item_code": item_code,
                            "qty": quantity,
                            "uom": uom,
                            "warehouse": warehouse,
                        }
                    ],
                }
            )
        return {
            "schema_version": "2",
            "action_type": action_type,
            "run_id": run_id,
            "action_id": str(uuid4()),
            "initiator": BUYER,
            "payload": payload,
            "evidence_refs": [f"erp:{source_doctype}:{source_name}"],
            "calculation_refs": [f"remaining:{source_row or source_name}"],
            "risk_class": "HIGH",
            "approval_class": "INDEPENDENT_APPROVER",
            "snapshot_ref": f"snapshot:{uuid4()}",
            "idempotency_key": f"p10-{action_type.lower()}-{uuid4().hex}",
            "expires_at": "2030-01-01T00:00:00Z",
            "revalidation_rule": "FULL_PRE_EXECUTE_RECHECK_P2P_V1",
            "summary": f"{action_type} {source_name}",
            "correlation_id": str(uuid4()),
        }

    def _approve_and_execute(self, proposal: dict[str, Any]) -> dict[str, Any]:
        frappe.set_user(BUYER)
        reviewed = cast(dict[str, Any], evaluate_proposal(proposal))
        self.assertTrue(reviewed["ok"], reviewed)
        frappe.set_user(RECEIVER)
        approved = cast(
            dict[str, Any],
            decide_action(
                proposal["action_id"],
                "ALLOW",
                reviewed["action"]["proposal_digest"],
                "independent receipt approval",
                str(uuid4()),
            ),
        )
        self.assertTrue(approved["ok"], approved)
        response = cast(
            dict[str, Any],
            execute_p2p_action(
                proposal["action_id"],
                reviewed["action"]["proposal_digest"],
                proposal["idempotency_key"],
                str(uuid4()),
            ),
        )
        self.assertTrue(response["ok"], response)
        return response

    def _review_and_approve(self, proposal: dict[str, Any]) -> dict[str, Any]:
        frappe.set_user(BUYER)
        reviewed = cast(dict[str, Any], evaluate_proposal(proposal))
        self.assertTrue(reviewed["ok"], reviewed)
        frappe.set_user(RECEIVER)
        approved = cast(
            dict[str, Any],
            decide_action(
                proposal["action_id"],
                "ALLOW",
                reviewed["action"]["proposal_digest"],
                "independent receipt approval",
                str(uuid4()),
            ),
        )
        self.assertTrue(approved["ok"], approved)
        return reviewed

    def test_partial_receipts_update_stock_and_po_remaining_qty(self) -> None:
        po_name, po_item_name, item_code = self._submitted_po()

        first_create = self._proposal(
            "CREATE_PR_DRAFT",
            "Purchase Order",
            po_name,
            source_row=po_item_name,
            item_code=item_code,
            quantity=1,
        )
        first_draft = self._approve_and_execute(first_create)
        self.assertEqual(first_draft["action"]["action_type"], "CREATE_PR_DRAFT")
        self.assertEqual(first_draft["target"]["doctype"], "Purchase Receipt")
        self.assertEqual(first_draft["target"]["docstatus"], 0)
        first_pr_name = str(first_draft["target"]["name"])
        first_pr = frappe.get_doc("Purchase Receipt", first_pr_name)
        self.assertEqual(str(first_pr.posting_date), "2026-09-10")
        self.assertEqual(first_pr.items[0].purchase_order_item, po_item_name)
        self.assertEqual(first_pr.items[0].qty, 1)

        first_submit = self._proposal("SUBMIT_PR", "Purchase Receipt", first_pr_name)
        submitted_first = self._approve_and_execute(first_submit)
        self.assertEqual(submitted_first["target"]["name"], first_pr_name)
        self.assertEqual(submitted_first["target"]["docstatus"], 1)
        self.assertEqual(
            frappe.db.get_value("Purchase Order Item", po_item_name, "received_qty"),
            1,
        )
        first_actual_qty = frappe.db.sql(
            """
            SELECT COALESCE(SUM(actual_qty), 0)
            FROM `tabStock Ledger Entry`
            WHERE voucher_no = %s AND is_cancelled = 0
            """,
            first_pr_name,
        )[0][0]
        self.assertEqual(float(first_actual_qty), 1.0)

        over_receipt = self._proposal(
            "CREATE_PR_DRAFT",
            "Purchase Order",
            po_name,
            source_row=po_item_name,
            item_code=item_code,
            quantity=2,
        )
        frappe.set_user(BUYER)
        rejected = cast(dict[str, Any], evaluate_proposal(over_receipt))
        self.assertTrue(rejected["ok"], rejected)
        self.assertEqual(rejected["action"]["state"], "POLICY_REJECTED")
        self.assertEqual(rejected["policy"]["outcome"], "REJECT")
        self.assertIn("remaining quantity", rejected["policy"]["reason"])

        invalid_uom = self._proposal(
            "CREATE_PR_DRAFT",
            "Purchase Order",
            po_name,
            source_row=po_item_name,
            item_code=item_code,
            quantity=1,
            uom="Kg",
        )
        frappe.set_user(BUYER)
        rejected_uom = cast(dict[str, Any], evaluate_proposal(invalid_uom))
        self.assertEqual(rejected_uom["action"]["state"], "POLICY_REJECTED")
        self.assertIn("UOM", rejected_uom["policy"]["reason"])

        invalid_warehouse = self._proposal(
            "CREATE_PR_DRAFT",
            "Purchase Order",
            po_name,
            source_row=po_item_name,
            item_code=item_code,
            quantity=1,
            warehouse="SYNORA-P1-Unknown - SP1",
        )
        frappe.set_user(BUYER)
        rejected_warehouse = cast(dict[str, Any], evaluate_proposal(invalid_warehouse))
        self.assertEqual(rejected_warehouse["action"]["state"], "POLICY_REJECTED")
        self.assertIn("warehouse", rejected_warehouse["policy"]["reason"])

        second_create = self._proposal(
            "CREATE_PR_DRAFT",
            "Purchase Order",
            po_name,
            source_row=po_item_name,
            item_code=item_code,
            quantity=1,
        )
        second_draft = self._approve_and_execute(second_create)
        second_pr_name = str(second_draft["target"]["name"])
        self.assertNotEqual(second_pr_name, first_pr_name)
        self.assertEqual(second_draft["target"]["docstatus"], 0)

        second_submit = self._proposal("SUBMIT_PR", "Purchase Receipt", second_pr_name)
        submitted_second = self._approve_and_execute(second_submit)
        self.assertEqual(submitted_second["target"]["docstatus"], 1)
        self.assertEqual(
            frappe.db.get_value("Purchase Order Item", po_item_name, "received_qty"),
            2,
        )
        self.assertEqual(
            frappe.db.get_value("Purchase Order", po_name, "per_received"),
            100.0,
        )
        total_actual_qty = frappe.db.sql(
            """
            SELECT COALESCE(SUM(actual_qty), 0)
            FROM `tabStock Ledger Entry`
            WHERE voucher_no IN (%s, %s) AND is_cancelled = 0
            """,
            (first_pr_name, second_pr_name),
        )[0][0]
        self.assertEqual(float(total_actual_qty), 2.0)

    def test_closed_purchase_order_is_rejected_before_receipt_insert(self) -> None:
        po_name, po_item_name, item_code = self._submitted_po()
        frappe.set_user("Administrator")
        po = frappe.get_doc("Purchase Order", po_name)
        po.update_status("Closed")
        frappe.db.commit()

        proposal = self._proposal(
            "CREATE_PR_DRAFT",
            "Purchase Order",
            po_name,
            source_row=po_item_name,
            item_code=item_code,
            quantity=1,
        )
        frappe.set_user(BUYER)
        reviewed = cast(dict[str, Any], evaluate_proposal(proposal))
        self.assertEqual(reviewed["action"]["state"], "POLICY_REJECTED")
        self.assertIn("closed", reviewed["policy"]["reason"])
        self.assertEqual(
            frappe.db.count("Purchase Receipt Item", {"purchase_order": po_name}),
            0,
        )

    def test_two_approved_receipts_cannot_consume_same_remaining_quantity(self) -> None:
        po_name, po_item_name, item_code = self._submitted_po()
        first = self._proposal(
            "CREATE_PR_DRAFT",
            "Purchase Order",
            po_name,
            source_row=po_item_name,
            item_code=item_code,
            quantity=1,
        )
        second = self._proposal(
            "CREATE_PR_DRAFT",
            "Purchase Order",
            po_name,
            source_row=po_item_name,
            item_code=item_code,
            quantity=1,
        )
        first_reviewed = self._review_and_approve(first)
        second_reviewed = self._review_and_approve(second)

        frappe.set_user(RECEIVER)
        first_response = cast(
            dict[str, Any],
            execute_p2p_action(
                first["action_id"],
                first_reviewed["action"]["proposal_digest"],
                first["idempotency_key"],
                str(uuid4()),
            ),
        )
        self.assertTrue(first_response["ok"], first_response)
        second_response = cast(
            dict[str, Any],
            execute_p2p_action(
                second["action_id"],
                second_reviewed["action"]["proposal_digest"],
                second["idempotency_key"],
                str(uuid4()),
            ),
        )
        self.assertFalse(second_response["ok"])
        self.assertEqual(second_response["error"]["code"], "CONFLICT")
        self.assertEqual(
            frappe.db.count("Purchase Receipt Item", {"purchase_order": po_name}),
            1,
        )
        self.assertEqual(
            frappe.db.get_value("Purchase Order Item", po_item_name, "received_qty"),
            0,
        )

    def test_failed_receipt_submit_preserves_draft_and_receipts_failure(self) -> None:
        po_name, po_item_name, item_code = self._submitted_po()
        create_proposal = self._proposal(
            "CREATE_PR_DRAFT",
            "Purchase Order",
            po_name,
            source_row=po_item_name,
            item_code=item_code,
            quantity=1,
        )
        draft_response = self._approve_and_execute(create_proposal)
        pr_name = str(draft_response["target"]["name"])
        submit_proposal = self._proposal("SUBMIT_PR", "Purchase Receipt", pr_name)
        reviewed = self._review_and_approve(submit_proposal)

        def failed_submit(_action: Any, _target: Any) -> Any:
            raise frappe.ValidationError("forced controller validation failure")

        frappe.set_user(RECEIVER)
        with patch.object(p2p_execution, "_apply_source_action", side_effect=failed_submit):
            response = cast(
                dict[str, Any],
                execute_p2p_action(
                    submit_proposal["action_id"],
                    reviewed["action"]["proposal_digest"],
                    submit_proposal["idempotency_key"],
                    str(uuid4()),
                ),
            )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "ERP_VALIDATION_ERROR")
        self.assertEqual(frappe.db.get_value("Purchase Receipt", pr_name, "docstatus"), 0)

        frappe.set_user(BUYER)
        run_details = cast(dict[str, Any], get_run(submit_proposal["run_id"]))
        self.assertTrue(run_details["ok"], run_details)
        governed = next(
            item
            for item in run_details["governance"]
            if item["action"]["action_id"] == submit_proposal["action_id"]
        )
        self.assertEqual(governed["receipt"]["final_state"], "FAILED")
        self.assertEqual(governed["receipt"]["target_name"], pr_name)

    def test_failed_receipt_insert_keeps_a_readable_failure_receipt(self) -> None:
        po_name, po_item_name, item_code = self._submitted_po()
        proposal = self._proposal(
            "CREATE_PR_DRAFT",
            "Purchase Order",
            po_name,
            source_row=po_item_name,
            item_code=item_code,
            quantity=1,
        )
        reviewed = self._review_and_approve(proposal)

        def invalid_target(action: Any) -> Any:
            target = p2p_execution._build_pr(action)
            target.supplier = ""
            return target

        frappe.set_user(RECEIVER)
        with patch.object(p2p_execution, "_build_target", side_effect=invalid_target):
            response = cast(
                dict[str, Any],
                execute_p2p_action(
                    proposal["action_id"],
                    reviewed["action"]["proposal_digest"],
                    proposal["idempotency_key"],
                    str(uuid4()),
                ),
            )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "ERP_VALIDATION_ERROR")

        frappe.set_user(BUYER)
        run_details = cast(dict[str, Any], get_run(proposal["run_id"]))
        self.assertTrue(run_details["ok"], run_details)
        governed = next(
            item
            for item in run_details["governance"]
            if item["action"]["action_id"] == proposal["action_id"]
        )
        self.assertEqual(governed["receipt"]["final_state"], "FAILED")
        self.assertIsNone(governed["receipt"]["target_name"])
