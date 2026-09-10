"""Real Frappe acceptance for governed partial Payment Entry settlement."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
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
ACCOUNTANT = "synora-p1-accountant@dev.localhost"
PAYMENT_OPERATOR = "synora-p1-payment-operator@dev.localhost"
PAYMENT_APPROVER = "synora-p1-payment-approver@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
SUPPLIER = "SYNORA-P1-Supplier-1"
PRICE_LIST = "SYNORA-P1 Buying CNY"
ITEM_GROUP = "SYNORA-P1 Items"
STOCK_UOM = "Unit"
PAID_FROM = "Cash - SP1"
PAID_TO = "Creditors - SP1"


class TestPhase10PaymentEntry(FrappeTestCase):  # type: ignore[misc]
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        frappe.set_user("Administrator")
        if not frappe.db.exists("User", PAYMENT_APPROVER):
            frappe.get_doc(
                {
                    "doctype": "User",
                    "email": PAYMENT_APPROVER,
                    "first_name": "Synora P1 Payment Approver",
                    "send_welcome_email": 0,
                    "enabled": 1,
                    "roles": [{"role": "Accounts User"}],
                }
            ).insert(ignore_permissions=True)
            frappe.db.commit()
        if not frappe.db.exists("User", PAYMENT_OPERATOR):
            frappe.get_doc(
                {
                    "doctype": "User",
                    "email": PAYMENT_OPERATOR,
                    "first_name": "Synora P1 Payment Operator",
                    "send_welcome_email": 0,
                    "enabled": 1,
                    "roles": [
                        {"role": "Accounts User"},
                        {"role": "Purchase User"},
                    ],
                }
            ).insert(ignore_permissions=True)
            frappe.db.commit()

    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        cancel_p10_test_documents()
        super().tearDown()

    def _submitted_invoice(self) -> tuple[str, str, str, str]:
        from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
        from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice

        item_code = f"SYNORA-P10-PE-{uuid4().hex[:12]}"
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
        pr = make_purchase_receipt(po.name)
        pr.items[0].qty = 2
        pr.insert(ignore_permissions=True)
        pr.submit()
        pi = make_purchase_invoice(pr.name)
        pi.insert(ignore_permissions=True)
        pi.submit()
        frappe.db.commit()
        return str(po.name), str(pr.name), str(pi.name), item_code

    def _proposal(
        self,
        action_type: str,
        source_name: str,
        *,
        initiator: str = BUYER,
        party: str = SUPPLIER,
        paid_amount: int = 10,
        paid_from: str = PAID_FROM,
        paid_to: str = PAID_TO,
        source_currency: str = "CNY",
        target_currency: str = "CNY",
    ) -> dict[str, Any]:
        if action_type == "SUBMIT_PAYMENT_ENTRY" and initiator == ACCOUNTANT:
            initiator = PAYMENT_OPERATOR
        frappe.set_user(initiator)
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
        if action_type == "CREATE_PAYMENT_ENTRY_DRAFT":
            payload: dict[str, Any] = {
                "company": COMPANY,
                "source_doctype": "Purchase Invoice",
                "source_name": source_name,
                "party_type": "Supplier",
                "party": party,
                "payment_type": "Pay",
                "posting_date": "2026-09-10",
                "paid_from": paid_from,
                "paid_to": paid_to,
                "paid_amount": paid_amount,
                "received_amount": paid_amount,
                "source_currency": source_currency,
                "target_currency": target_currency,
                "references": [
                    {
                        "reference_doctype": "Purchase Invoice",
                        "reference_name": source_name,
                        "allocated_amount": paid_amount,
                    }
                ],
            }
        else:
            payload = {
                "company": COMPANY,
                "source_doctype": "Payment Entry",
                "source_name": source_name,
            }
        return {
            "schema_version": "2",
            "action_type": action_type,
            "run_id": run_id,
            "action_id": str(uuid4()),
            "initiator": initiator,
            "payload": payload,
            "evidence_refs": [f"erp:{payload['source_doctype']}:{source_name}"],
            "calculation_refs": [f"outstanding:{source_name}"],
            "risk_class": "HIGH",
            "approval_class": "INDEPENDENT_APPROVER",
            "snapshot_ref": f"snapshot:{uuid4()}",
            "idempotency_key": f"p10-{action_type.lower()}-{uuid4().hex}",
            "expires_at": "2030-01-01T00:00:00Z",
            "revalidation_rule": "FULL_PRE_EXECUTE_RECHECK_P2P_V1",
            "summary": f"{action_type} {source_name}",
            "correlation_id": str(uuid4()),
        }

    def _review_and_approve(self, proposal: dict[str, Any]) -> dict[str, Any]:
        approver = (
            PAYMENT_APPROVER if proposal["action_type"] == "SUBMIT_PAYMENT_ENTRY" else ACCOUNTANT
        )
        frappe.set_user(str(proposal["initiator"]))
        reviewed = cast(dict[str, Any], evaluate_proposal(proposal))
        self.assertTrue(reviewed["ok"], reviewed)
        frappe.set_user(approver)
        approved = cast(
            dict[str, Any],
            decide_action(
                proposal["action_id"],
                "ALLOW",
                reviewed["action"]["proposal_digest"],
                "independent payment approval",
                str(uuid4()),
            ),
        )
        self.assertTrue(approved["ok"], approved)
        return reviewed

    def _approve_and_execute(self, proposal: dict[str, Any]) -> dict[str, Any]:
        reviewed = self._review_and_approve(proposal)
        executor = (
            PAYMENT_APPROVER if proposal["action_type"] == "SUBMIT_PAYMENT_ENTRY" else ACCOUNTANT
        )
        frappe.set_user(executor)
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

    def test_partial_payments_update_invoice_outstanding_and_gl(self) -> None:
        _po_name, _pr_name, pi_name, _item_code = self._submitted_invoice()
        first = self._proposal("CREATE_PAYMENT_ENTRY_DRAFT", pi_name)
        first_draft = self._approve_and_execute(first)
        self.assertEqual(first_draft["target"]["doctype"], "Payment Entry")
        self.assertEqual(first_draft["target"]["docstatus"], 0)
        self.assertEqual(first_draft["action"]["calculation"]["total_amount"], "10")
        pe_name = str(first_draft["target"]["name"])
        pe = frappe.get_doc("Payment Entry", pe_name)
        self.assertEqual(pe.party_type, "Supplier")
        self.assertEqual(pe.party, SUPPLIER)
        self.assertEqual(pe.payment_type, "Pay")
        self.assertEqual(pe.paid_from, PAID_FROM)
        self.assertEqual(pe.paid_to, PAID_TO)
        self.assertEqual(pe.paid_amount, 10)
        self.assertEqual(pe.references[0].reference_name, pi_name)
        self.assertEqual(pe.references[0].allocated_amount, 10)

        submit = self._proposal("SUBMIT_PAYMENT_ENTRY", pe_name, initiator=PAYMENT_OPERATOR)
        submit_review = self._review_and_approve(submit)
        frappe.set_user(PAYMENT_APPROVER)
        submitted = cast(
            dict[str, Any],
            execute_p2p_action(
                submit["action_id"],
                submit_review["action"]["proposal_digest"],
                submit["idempotency_key"],
                str(uuid4()),
            ),
        )
        self.assertTrue(submitted["ok"], submitted)
        self.assertEqual(submitted["target"]["docstatus"], 1)
        verified = submitted["receipt"]["verified_fields"]
        self.assertEqual(verified["status"], "Submitted")
        self.assertEqual(verified["paid_amount"], "10")
        self.assertEqual(verified["total_allocated_amount"], "10")
        self.assertGreater(verified["gl_entry_count"], 0)
        self.assertEqual(verified["gl_debit_total"], verified["gl_credit_total"])
        self.assertEqual(verified["reference_name"], pi_name)
        self.assertEqual(verified["purchase_invoice_status"], "Partly Paid")
        self.assertEqual(verified["purchase_invoice_outstanding_amount"], "10")
        self.assertEqual(frappe.db.get_value("Purchase Invoice", pi_name, "status"), "Partly Paid")
        self.assertEqual(
            float(frappe.db.get_value("Purchase Invoice", pi_name, "outstanding_amount")),
            10.0,
        )
        self.assertGreater(
            frappe.db.count("GL Entry", {"voucher_type": "Payment Entry", "voucher_no": pe_name}),
            0,
        )

        second = self._approve_and_execute(self._proposal("CREATE_PAYMENT_ENTRY_DRAFT", pi_name))
        second_submit = self._approve_and_execute(
            self._proposal(
                "SUBMIT_PAYMENT_ENTRY",
                str(second["target"]["name"]),
                initiator=PAYMENT_OPERATOR,
            )
        )
        self.assertEqual(second_submit["target"]["docstatus"], 1)
        self.assertEqual(frappe.db.get_value("Purchase Invoice", pi_name, "status"), "Paid")
        self.assertEqual(
            float(frappe.db.get_value("Purchase Invoice", pi_name, "outstanding_amount")),
            0.0,
        )
        frappe.set_user(PAYMENT_APPROVER)
        replay = cast(
            dict[str, Any],
            execute_p2p_action(
                submit["action_id"],
                submitted["action"]["proposal_digest"],
                submit["idempotency_key"],
                str(uuid4()),
            ),
        )
        self.assertEqual(replay["target"]["name"], pe_name)
        frappe.set_user(PAYMENT_OPERATOR)
        self.assertTrue(cast(dict[str, Any], get_run(submit["run_id"]))["ok"])

    def test_payment_policy_rejects_overpayment_and_wrong_party(self) -> None:
        _po_name, _pr_name, pi_name, _item_code = self._submitted_invoice()
        for proposal in (
            self._proposal("CREATE_PAYMENT_ENTRY_DRAFT", pi_name, paid_amount=21),
            self._proposal("CREATE_PAYMENT_ENTRY_DRAFT", pi_name, party="SYNORA-P1-Supplier-2"),
            self._proposal("CREATE_PAYMENT_ENTRY_DRAFT", pi_name, paid_from="Creditors - SP1"),
            self._proposal("CREATE_PAYMENT_ENTRY_DRAFT", pi_name, source_currency="USD"),
            self._proposal("CREATE_PAYMENT_ENTRY_DRAFT", pi_name, target_currency="USD"),
        ):
            frappe.set_user(BUYER)
            rejected = cast(dict[str, Any], evaluate_proposal(proposal))
            self.assertEqual(rejected["action"]["state"], "POLICY_REJECTED")

    def test_two_approved_payment_drafts_cannot_consume_same_outstanding(self) -> None:
        _po_name, _pr_name, pi_name, _item_code = self._submitted_invoice()
        first = self._proposal("CREATE_PAYMENT_ENTRY_DRAFT", pi_name)
        second = self._proposal("CREATE_PAYMENT_ENTRY_DRAFT", pi_name)
        first_review = self._review_and_approve(first)
        second_review = self._review_and_approve(second)
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
                try:
                    return cast(
                        dict[str, Any],
                        execute_p2p_action(
                            proposal["action_id"],
                            reviewed["action"]["proposal_digest"],
                            proposal["idempotency_key"],
                            str(uuid4()),
                        ),
                    )
                except frappe.ValidationError as error:
                    return {"ok": False, "error": {"code": type(error).__name__}}
            finally:
                frappe.db.rollback()
                frappe.destroy()

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    execute_in_separate_connection,
                    (first, second),
                    (first_review, second_review),
                )
            )
        successes = [response for response in responses if response.get("ok")]
        conflicts = [response for response in responses if not response.get("ok")]
        self.assertEqual(len(successes), 1, responses)
        self.assertEqual(len(conflicts), 1, responses)
        self.assertEqual(conflicts[0]["error"]["code"], "CONFLICT")
        frappe.db.commit()
        self.assertEqual(
            frappe.db.count("Payment Entry Reference", {"reference_name": pi_name}),
            1,
        )

    def test_failed_payment_submit_preserves_draft_and_receipt(self) -> None:
        _po_name, _pr_name, pi_name, _item_code = self._submitted_invoice()
        draft = self._approve_and_execute(self._proposal("CREATE_PAYMENT_ENTRY_DRAFT", pi_name))
        pe_name = str(draft["target"]["name"])
        submit = self._proposal("SUBMIT_PAYMENT_ENTRY", pe_name, initiator=PAYMENT_OPERATOR)
        reviewed = self._review_and_approve(submit)

        def failed_submit(_action: Any, _target: Any) -> Any:
            raise frappe.ValidationError("forced payment controller validation failure")

        frappe.set_user(PAYMENT_APPROVER)
        with patch.object(p2p_execution, "_apply_source_action", side_effect=failed_submit):
            response = cast(
                dict[str, Any],
                execute_p2p_action(
                    submit["action_id"],
                    reviewed["action"]["proposal_digest"],
                    submit["idempotency_key"],
                    str(uuid4()),
                ),
            )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "ERP_VALIDATION_ERROR")
        self.assertEqual(frappe.db.get_value("Payment Entry", pe_name, "docstatus"), 0)
        frappe.set_user(PAYMENT_OPERATOR)
        run_details = cast(dict[str, Any], get_run(submit["run_id"]))
        governed = next(
            item
            for item in run_details["governance"]
            if item["action"]["action_id"] == submit["action_id"]
        )
        self.assertEqual(governed["receipt"]["final_state"], "FAILED")
        self.assertEqual(governed["receipt"]["target_name"], pe_name)
