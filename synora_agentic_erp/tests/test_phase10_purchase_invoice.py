"""Real Frappe acceptance for governed partial Purchase Invoices."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any, cast
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

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
ACCOUNTANT = "synora-p1-accountant@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
SUPPLIER = "SYNORA-P1-Supplier-1"
PRICE_LIST = "SYNORA-P1 Buying CNY"
ITEM_GROUP = "SYNORA-P1 Items"
STOCK_UOM = "Unit"
P10_TEST_DATE = today()


class TestPhase10PurchaseInvoice(FrappeTestCase):  # type: ignore[misc]
    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        cancel_p10_test_documents()
        super().tearDown()

    def _submitted_receipt(self) -> tuple[str, str, str, str]:
        from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

        item_code = f"SYNORA-P10-PI-{uuid4().hex[:12]}"
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
                "transaction_date": P10_TEST_DATE,
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
        pr = make_purchase_receipt(po.name)
        pr.items[0].qty = 2
        pr.insert(ignore_permissions=True)
        pr.submit()
        frappe.db.commit()
        return str(po.name), str(pr.name), str(pr.items[0].name), item_code

    def _proposal(
        self,
        action_type: str,
        source_name: str,
        *,
        source_row: str | None = None,
        item_code: str | None = None,
        quantity: int | None = None,
        rate: int = 10,
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
            "source_doctype": (
                "Purchase Invoice" if action_type == "SUBMIT_PI" else "Purchase Receipt"
            ),
            "source_name": source_name,
        }
        if action_type == "CREATE_PI_DRAFT":
            payload.update(
                {
                    "transaction_date": P10_TEST_DATE,
                    "items": [
                        {
                            "source_row": source_row,
                            "item_code": item_code,
                            "qty": quantity,
                            "uom": uom,
                            "warehouse": warehouse,
                            "rate": rate,
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
            "evidence_refs": [f"erp:{payload['source_doctype']}:{source_name}"],
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
        reviewed = self._review_and_approve(proposal)
        frappe.set_user(ACCOUNTANT)
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
        frappe.set_user(ACCOUNTANT)
        approved = cast(
            dict[str, Any],
            decide_action(
                proposal["action_id"],
                "ALLOW",
                reviewed["action"]["proposal_digest"],
                "independent invoice approval",
                str(uuid4()),
            ),
        )
        self.assertTrue(approved["ok"], approved)
        return reviewed

    def test_partial_invoices_update_receipt_billing_and_accounting(self) -> None:
        po_name, pr_name, pr_item_name, item_code = self._submitted_receipt()
        first = self._proposal(
            "CREATE_PI_DRAFT",
            pr_name,
            source_row=pr_item_name,
            item_code=item_code,
            quantity=1,
        )
        first_draft = self._approve_and_execute(first)
        self.assertEqual(first_draft["target"]["doctype"], "Purchase Invoice")
        self.assertEqual(first_draft["target"]["docstatus"], 0)
        self.assertEqual(first_draft["action"]["calculation"]["total_amount"], "10")
        self.assertEqual(first_draft["action"]["calculation"]["status"], "Draft")
        first_pi_name = str(first_draft["target"]["name"])
        first_pi = frappe.get_doc("Purchase Invoice", first_pi_name)
        self.assertEqual(str(first_pi.posting_date), P10_TEST_DATE)
        self.assertEqual(first_pi.items[0].pr_detail, pr_item_name)
        self.assertEqual(first_pi.items[0].purchase_receipt, pr_name)
        self.assertEqual(first_pi.items[0].qty, 1)
        self.assertEqual(first_pi.items[0].rate, 10)
        self.assertEqual(first_pi.items[0].uom, STOCK_UOM)
        self.assertEqual(first_pi.items[0].warehouse, WAREHOUSE)

        submit_first = self._proposal("SUBMIT_PI", first_pi_name)
        frappe.set_user(BUYER)
        submit_preview = cast(dict[str, Any], evaluate_proposal(submit_first))
        self.assertEqual(submit_preview["action"]["calculation"]["grand_total"], "10")
        self.assertEqual(submit_preview["action"]["calculation"]["outstanding_amount"], "10")
        frappe.set_user(ACCOUNTANT)
        approved_submit = cast(
            dict[str, Any],
            decide_action(
                submit_first["action_id"],
                "ALLOW",
                submit_preview["action"]["proposal_digest"],
                "independent invoice approval",
                str(uuid4()),
            ),
        )
        self.assertTrue(approved_submit["ok"], approved_submit)
        submitted_first = cast(
            dict[str, Any],
            execute_p2p_action(
                submit_first["action_id"],
                submit_preview["action"]["proposal_digest"],
                submit_first["idempotency_key"],
                str(uuid4()),
            ),
        )
        self.assertTrue(submitted_first["ok"], submitted_first)
        self.assertEqual(submitted_first["target"]["docstatus"], 1)
        verified = submitted_first["receipt"]["verified_fields"]
        self.assertEqual(verified["status"], "Unpaid")
        self.assertEqual(verified["grand_total"], "10")
        self.assertEqual(verified["outstanding_amount"], "10")
        self.assertGreater(verified["gl_entry_count"], 0)
        self.assertEqual(verified["gl_debit_total"], verified["gl_credit_total"])
        self.assertEqual(verified["item_0.purchase_receipt"], pr_name)
        self.assertEqual(verified["item_0.pr_billed_amt"], "10")
        self.assertEqual(frappe.db.get_value("Purchase Invoice", first_pi_name, "status"), "Unpaid")
        self.assertEqual(
            float(frappe.db.get_value("Purchase Invoice", first_pi_name, "outstanding_amount")),
            10.0,
        )
        self.assertEqual(
            frappe.db.get_value("Purchase Receipt Item", pr_item_name, "billed_amt"), 10.0
        )
        self.assertEqual(frappe.db.get_value("Purchase Receipt", pr_name, "per_billed"), 50.0)
        self.assertGreater(
            frappe.db.count(
                "GL Entry", {"voucher_type": "Purchase Invoice", "voucher_no": first_pi_name}
            ),
            0,
        )
        frappe.set_user(ACCOUNTANT)
        replay = cast(
            dict[str, Any],
            execute_p2p_action(
                submit_first["action_id"],
                submitted_first["action"]["proposal_digest"],
                submit_first["idempotency_key"],
                str(uuid4()),
            ),
        )
        self.assertEqual(replay["target"]["name"], first_pi_name)

        over = self._proposal(
            "CREATE_PI_DRAFT",
            pr_name,
            source_row=pr_item_name,
            item_code=item_code,
            quantity=2,
        )
        frappe.set_user(BUYER)
        rejected = cast(dict[str, Any], evaluate_proposal(over))
        self.assertEqual(rejected["action"]["state"], "POLICY_REJECTED")
        self.assertIn("remaining quantity", rejected["policy"]["reason"])

        invalid_rate = self._proposal(
            "CREATE_PI_DRAFT",
            pr_name,
            source_row=pr_item_name,
            item_code=item_code,
            quantity=1,
            rate=9,
        )
        invalid_uom = self._proposal(
            "CREATE_PI_DRAFT",
            pr_name,
            source_row=pr_item_name,
            item_code=item_code,
            quantity=1,
            uom="Kg",
        )
        invalid_warehouse = self._proposal(
            "CREATE_PI_DRAFT",
            pr_name,
            source_row=pr_item_name,
            item_code=item_code,
            quantity=1,
            warehouse="SYNORA-P1-Unknown - SP1",
        )
        for proposal, marker in (
            (invalid_rate, "rate"),
            (invalid_uom, "UOM"),
            (invalid_warehouse, "warehouse"),
        ):
            frappe.set_user(BUYER)
            rejected = cast(dict[str, Any], evaluate_proposal(proposal))
            self.assertEqual(rejected["action"]["state"], "POLICY_REJECTED")
            self.assertIn(marker, rejected["policy"]["reason"])

        second = self._proposal(
            "CREATE_PI_DRAFT",
            pr_name,
            source_row=pr_item_name,
            item_code=item_code,
            quantity=1,
        )
        second_draft = self._approve_and_execute(second)
        second_pi_name = str(second_draft["target"]["name"])
        self.assertNotEqual(second_pi_name, first_pi_name)
        submitted_second = self._approve_and_execute(self._proposal("SUBMIT_PI", second_pi_name))
        self.assertEqual(submitted_second["target"]["docstatus"], 1)
        self.assertEqual(
            frappe.db.get_value("Purchase Receipt Item", pr_item_name, "billed_amt"), 20.0
        )
        self.assertEqual(frappe.db.get_value("Purchase Receipt", pr_name, "per_billed"), 100.0)
        self.assertEqual(frappe.db.get_value("Purchase Order", po_name, "per_billed"), 100.0)
        frappe.set_user(BUYER)
        historical_run = cast(dict[str, Any], get_run(submit_first["run_id"]))
        self.assertTrue(historical_run["ok"], historical_run)

    def test_buying_settings_controls_rejected_quantity_billing_basis(self) -> None:
        _po_name, pr_name, pr_item_name, item_code = self._submitted_receipt()
        proposal = self._proposal(
            "CREATE_PI_DRAFT",
            pr_name,
            source_row=pr_item_name,
            item_code=item_code,
            quantity=1,
        )
        original_get_list = frappe.get_list
        original_get_single_value = frappe.db.get_single_value

        def rejected_quantity_source(doctype: str, *args: Any, **kwargs: Any) -> Any:
            rows = original_get_list(doctype, *args, **kwargs)
            if doctype == "Purchase Receipt Item":
                for row in rows:
                    row.received_qty = 0
            return rows

        def bill_rejected_quantity(doctype: str, fieldname: str, *args: Any, **kwargs: Any) -> Any:
            if (
                doctype == "Buying Settings"
                and fieldname == "bill_for_rejected_quantity_in_purchase_invoice"
            ):
                return 1
            return original_get_single_value(doctype, fieldname, *args, **kwargs)

        frappe.set_user(BUYER)
        with (
            patch.object(frappe, "get_list", side_effect=rejected_quantity_source),
            patch.object(frappe.db, "get_single_value", side_effect=bill_rejected_quantity),
        ):
            reviewed = cast(dict[str, Any], evaluate_proposal(proposal))
        self.assertEqual(reviewed["action"]["state"], "POLICY_REJECTED")
        self.assertIn("remaining quantity", reviewed["policy"]["reason"])

        false_proposal = self._proposal(
            "CREATE_PI_DRAFT",
            pr_name,
            source_row=pr_item_name,
            item_code=item_code,
            quantity=2,
        )

        def ordered_quantity_source(doctype: str, *args: Any, **kwargs: Any) -> Any:
            rows = original_get_list(doctype, *args, **kwargs)
            if doctype == "Purchase Receipt Item":
                for row in rows:
                    row.received_qty = 0
                    row.rejected_qty = 1
            return rows

        def do_not_bill_rejected_quantity(
            doctype: str, fieldname: str, *args: Any, **kwargs: Any
        ) -> Any:
            if (
                doctype == "Buying Settings"
                and fieldname == "bill_for_rejected_quantity_in_purchase_invoice"
            ):
                return 0
            return original_get_single_value(doctype, fieldname, *args, **kwargs)

        frappe.set_user(BUYER)
        with (
            patch.object(frappe, "get_list", side_effect=ordered_quantity_source),
            patch.object(frappe.db, "get_single_value", side_effect=do_not_bill_rejected_quantity),
        ):
            reviewed_false = cast(dict[str, Any], evaluate_proposal(false_proposal))
        # This follows ERPNext's upstream branch: when the setting is false,
        # the ordered qty is the basis; rejected_qty only adjusts a returned
        # quantity that is actually present.
        self.assertEqual(reviewed_false["action"]["state"], "AWAITING_APPROVAL")

    def test_two_approved_invoices_cannot_consume_same_remaining_quantity(self) -> None:
        _po_name, pr_name, pr_item_name, item_code = self._submitted_receipt()
        first = self._proposal(
            "CREATE_PI_DRAFT",
            pr_name,
            source_row=pr_item_name,
            item_code=item_code,
            quantity=1,
        )
        second = self._proposal(
            "CREATE_PI_DRAFT",
            pr_name,
            source_row=pr_item_name,
            item_code=item_code,
            quantity=1,
        )
        first_reviewed = self._review_and_approve(first)
        second_reviewed = self._review_and_approve(second)
        # Separate connections must see the approved governance facts before
        # they race on the shared Purchase Receipt Item row.
        frappe.db.commit()

        site = frappe.local.site
        barrier = Barrier(2)

        def execute_in_separate_connection(
            proposal: dict[str, Any], reviewed: dict[str, Any]
        ) -> dict[str, Any]:
            frappe.init(site=site)
            frappe.connect()
            try:
                frappe.set_user(ACCOUNTANT)
                barrier.wait(timeout=10)
                return cast(
                    dict[str, Any],
                    execute_p2p_action(
                        proposal["action_id"],
                        reviewed["action"]["proposal_digest"],
                        proposal["idempotency_key"],
                        str(uuid4()),
                    ),
                )
            finally:
                frappe.db.rollback()
                frappe.destroy()

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    execute_in_separate_connection,
                    (first, second),
                    (first_reviewed, second_reviewed),
                )
            )
        successes = [response for response in responses if response.get("ok")]
        conflicts = [response for response in responses if not response.get("ok")]
        self.assertEqual(len(successes), 1, responses)
        self.assertEqual(len(conflicts), 1, responses)
        self.assertEqual(conflicts[0]["error"]["code"], "CONFLICT")
        frappe.db.commit()
        self.assertEqual(
            frappe.db.count("Purchase Invoice Item", {"purchase_receipt": pr_name}),
            1,
        )
        self.assertEqual(
            frappe.db.get_value("Purchase Receipt Item", pr_item_name, "billed_amt"), 0
        )

    def test_failed_invoice_submit_preserves_draft_and_receipts_failure(self) -> None:
        _po_name, pr_name, pr_item_name, item_code = self._submitted_receipt()
        create_proposal = self._proposal(
            "CREATE_PI_DRAFT",
            pr_name,
            source_row=pr_item_name,
            item_code=item_code,
            quantity=1,
        )
        draft_response = self._approve_and_execute(create_proposal)
        pi_name = str(draft_response["target"]["name"])
        submit_proposal = self._proposal("SUBMIT_PI", pi_name)
        reviewed = self._review_and_approve(submit_proposal)

        def failed_submit(_action: Any, _target: Any) -> Any:
            raise frappe.ValidationError("forced invoice controller validation failure")

        frappe.set_user(ACCOUNTANT)
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
        self.assertEqual(frappe.db.get_value("Purchase Invoice", pi_name, "docstatus"), 0)
        frappe.set_user(BUYER)
        run_details = cast(dict[str, Any], get_run(submit_proposal["run_id"]))
        governed = next(
            item
            for item in run_details["governance"]
            if item["action"]["action_id"] == submit_proposal["action_id"]
        )
        self.assertEqual(governed["receipt"]["final_state"], "FAILED")
        self.assertEqual(governed["receipt"]["target_name"], pi_name)

    def test_failed_invoice_insert_keeps_a_readable_failure_receipt(self) -> None:
        _po_name, pr_name, pr_item_name, item_code = self._submitted_receipt()
        proposal = self._proposal(
            "CREATE_PI_DRAFT",
            pr_name,
            source_row=pr_item_name,
            item_code=item_code,
            quantity=1,
        )
        reviewed = self._review_and_approve(proposal)

        def invalid_target(action: Any) -> Any:
            target = p2p_execution._build_pi(action)
            target.supplier = ""
            return target

        frappe.set_user(ACCOUNTANT)
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
        governed = next(
            item
            for item in run_details["governance"]
            if item["action"]["action_id"] == proposal["action_id"]
        )
        self.assertEqual(governed["receipt"]["final_state"], "FAILED")
        self.assertIsNone(governed["receipt"]["target_name"])
