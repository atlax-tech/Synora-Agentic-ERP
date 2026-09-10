"""Real ERP acceptance for governed P2P cancellation and recovery."""

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
APPROVER = "synora-p1-approver@dev.localhost"
RECEIVER = "synora-p1-receiver@dev.localhost"
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


class TestPhase10Cancellation(FrappeTestCase):  # type: ignore[misc]
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        frappe.set_user("Administrator")
        users = (
            (RECEIVER, "Synora P1 Receiver", ["Stock User", "Purchase User"]),
            (ACCOUNTANT, "Synora P1 Accountant", ["Accounts User"]),
            (
                PAYMENT_OPERATOR,
                "Synora P1 Payment Operator",
                ["Accounts User", "Purchase User"],
            ),
            (PAYMENT_APPROVER, "Synora P1 Payment Approver", ["Accounts User"]),
        )
        for email, first_name, roles in users:
            if frappe.db.exists("User", email):
                continue
            frappe.get_doc(
                {
                    "doctype": "User",
                    "email": email,
                    "first_name": first_name,
                    "send_welcome_email": 0,
                    "enabled": 1,
                    "roles": [{"role": role} for role in roles],
                }
            ).insert(ignore_permissions=True)
        frappe.db.commit()

    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        cancel_p10_test_documents()
        super().tearDown()

    def _submitted_chain(self, *, payment: bool = False) -> tuple[str, str, str, str, str | None]:
        from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
        from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
        from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice

        item_code = f"SYNORA-P10-CANCEL-{uuid4().hex[:12]}"
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
        pe_name: str | None = None
        if payment:
            pe = get_payment_entry(
                "Purchase Invoice",
                pi.name,
                party_amount=10,
                bank_account=PAID_FROM,
                party_type="Supplier",
                payment_type="Pay",
                reference_date="2026-09-10",
            )
            pe.insert(ignore_permissions=True)
            pe.submit()
            pe_name = str(pe.name)
        frappe.db.commit()
        return str(po.name), str(pr.name), str(pi.name), item_code, pe_name

    def _proposal(
        self,
        action_type: str,
        source_doctype: str,
        source_name: str,
        *,
        initiator: str,
    ) -> dict[str, Any]:
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
        return {
            "schema_version": "2",
            "action_type": action_type,
            "run_id": run_id,
            "action_id": str(uuid4()),
            "initiator": initiator,
            "payload": {
                "company": COMPANY,
                "source_doctype": source_doctype,
                "source_name": source_name,
                "reason": "受治理逆序取消并核对库存或会计逆向结果",
            },
            "evidence_refs": [f"erp:{source_doctype}:{source_name}"],
            "calculation_refs": [f"state:{source_name}"],
            "risk_class": "HIGH",
            "approval_class": "INDEPENDENT_APPROVER",
            "snapshot_ref": f"snapshot:{uuid4()}",
            "idempotency_key": f"p10-{action_type.lower()}-{uuid4().hex}",
            "expires_at": "2030-01-01T00:00:00Z",
            "revalidation_rule": "FULL_PRE_EXECUTE_RECHECK_P2P_V1",
            "summary": f"{action_type} {source_name}",
            "correlation_id": str(uuid4()),
        }

    def _approve(
        self, proposal: dict[str, Any], approver: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
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
                "independent cancellation approval",
                str(uuid4()),
            ),
        )
        self.assertTrue(approved["ok"], approved)
        return reviewed, approved

    def _execute(
        self, proposal: dict[str, Any], approver: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        reviewed, _approved = self._approve(proposal, approver)
        frappe.set_user(approver)
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
        return reviewed, response

    def test_submitted_purchase_order_without_downstream_can_be_cancelled(self) -> None:
        po_name, _pr_name, _pi_name, _item_code, _pe_name = self._submitted_chain(payment=False)
        # The fixture has a submitted PO but also creates downstream documents;
        # cancel them natively first to establish the legal no-downstream state.
        frappe.set_user("Administrator")
        frappe.get_doc("Purchase Invoice", _pi_name).cancel()
        frappe.get_doc("Purchase Receipt", _pr_name).cancel()
        frappe.db.commit()
        proposal = self._proposal(
            "CANCEL_PO", "Purchase Order", po_name, initiator=BUYER
        )
        reviewed, response = self._execute(proposal, APPROVER)
        self.assertEqual(response["action"]["calculation"]["status"], "Cancelled")
        self.assertEqual(response["target"]["docstatus"], 2)
        self.assertEqual(response["receipt"]["verified_fields"]["status"], "Cancelled")
        self.assertEqual(response["receipt"]["verified_fields"]["per_received"], "0")
        self.assertEqual(response["receipt"]["verified_fields"]["per_billed"], "0")
        self.assertEqual(frappe.db.get_value("Purchase Order", po_name, "docstatus"), 2)

        frappe.set_user(APPROVER)
        replay = cast(
            dict[str, Any],
            execute_p2p_action(
                proposal["action_id"],
                reviewed["action"]["proposal_digest"],
                proposal["idempotency_key"],
                str(uuid4()),
            ),
        )
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["target"]["name"], po_name)

    def test_downstream_blockers_and_legal_reverse_order(self) -> None:
        po_name, pr_name, pi_name, _item_code, pe_name = self._submitted_chain(payment=True)
        assert pe_name is not None

        po_blocked = self._proposal("CANCEL_PO", "Purchase Order", po_name, initiator=BUYER)
        frappe.set_user(BUYER)
        rejected_po = cast(dict[str, Any], evaluate_proposal(po_blocked))
        self.assertTrue(rejected_po["ok"], rejected_po)
        self.assertEqual(rejected_po["action"]["state"], "POLICY_REJECTED")
        self.assertIn("Purchase Receipt", rejected_po["policy"]["reason"])
        self.assertEqual(frappe.db.get_value("Purchase Order", po_name, "docstatus"), 1)

        pr_blocked = self._proposal(
            "CANCEL_PR", "Purchase Receipt", pr_name, initiator=PAYMENT_OPERATOR
        )
        frappe.set_user(PAYMENT_OPERATOR)
        rejected_pr = cast(dict[str, Any], evaluate_proposal(pr_blocked))
        self.assertTrue(rejected_pr["ok"], rejected_pr)
        self.assertEqual(rejected_pr["action"]["state"], "POLICY_REJECTED")
        self.assertIn("Purchase Invoice", rejected_pr["policy"]["reason"])

        pi_blocked = self._proposal(
            "CANCEL_PI", "Purchase Invoice", pi_name, initiator=PAYMENT_OPERATOR
        )
        frappe.set_user(PAYMENT_OPERATOR)
        rejected_pi = cast(dict[str, Any], evaluate_proposal(pi_blocked))
        self.assertTrue(rejected_pi["ok"], rejected_pi)
        self.assertEqual(rejected_pi["action"]["state"], "POLICY_REJECTED")
        self.assertIn("Payment Entry", rejected_pi["policy"]["reason"])

        pe_proposal = self._proposal(
            "CANCEL_PAYMENT_ENTRY", "Payment Entry", pe_name, initiator=PAYMENT_OPERATOR
        )
        _pe_reviewed, pe_response = self._execute(pe_proposal, PAYMENT_APPROVER)
        self.assertEqual(pe_response["target"]["docstatus"], 2)
        pe_verified = pe_response["receipt"]["verified_fields"]
        self.assertEqual(pe_verified["status"], "Cancelled")
        self.assertEqual(pe_verified["purchase_invoice_status"], "Unpaid")
        self.assertEqual(pe_verified["purchase_invoice_outstanding_amount"], "20")
        self.assertEqual(pe_verified["gl_net_debit_minus_credit"], "0")

        pi_proposal = self._proposal(
            "CANCEL_PI", "Purchase Invoice", pi_name, initiator=PAYMENT_OPERATOR
        )
        _pi_reviewed, pi_response = self._execute(pi_proposal, ACCOUNTANT)
        self.assertEqual(pi_response["target"]["docstatus"], 2)
        pi_verified = pi_response["receipt"]["verified_fields"]
        self.assertEqual(pi_verified["status"], "Cancelled")
        self.assertEqual(pi_verified["gl_net_debit_minus_credit"], "0")
        self.assertEqual(frappe.db.get_value("Purchase Receipt", pr_name, "per_billed"), 0)

        pr_proposal = self._proposal(
            "CANCEL_PR", "Purchase Receipt", pr_name, initiator=RECEIVER
        )
        pr_reviewed, pr_response = self._execute(pr_proposal, BUYER)
        self.assertEqual(pr_response["target"]["docstatus"], 2)
        pr_verified = pr_response["receipt"]["verified_fields"]
        self.assertEqual(pr_verified["status"], "Cancelled")
        self.assertEqual(pr_verified["stock_actual_qty_net"], "0")
        self.assertEqual(
            frappe.db.get_value("Purchase Order Item", {"parent": po_name}, "received_qty"),
            0,
        )

        frappe.set_user(BUYER)
        pr_replay = cast(
            dict[str, Any],
            execute_p2p_action(
                pr_proposal["action_id"],
                pr_reviewed["action"]["proposal_digest"],
                pr_proposal["idempotency_key"],
                str(uuid4()),
            ),
        )
        self.assertTrue(pr_replay["ok"], pr_replay)
        self.assertEqual(pr_replay["target"]["name"], pr_name)

        po_proposal = self._proposal("CANCEL_PO", "Purchase Order", po_name, initiator=BUYER)
        _po_reviewed, po_response = self._execute(po_proposal, APPROVER)
        self.assertEqual(po_response["target"]["docstatus"], 2)
        po_verified = po_response["receipt"]["verified_fields"]
        self.assertEqual(po_verified["status"], "Cancelled")
        self.assertEqual(po_verified["per_received"], "0")
        self.assertEqual(po_verified["per_billed"], "0")

    def test_cancel_controller_failure_keeps_document_and_records_recovery(self) -> None:
        po_name, pr_name, pi_name, _item_code, _pe_name = self._submitted_chain(payment=False)
        frappe.set_user("Administrator")
        frappe.get_doc("Purchase Invoice", pi_name).cancel()
        frappe.get_doc("Purchase Receipt", pr_name).cancel()
        frappe.db.commit()
        proposal = self._proposal("CANCEL_PO", "Purchase Order", po_name, initiator=BUYER)
        reviewed, _approved = self._approve(proposal, APPROVER)

        def failed_cancel(_action: Any, _target: Any) -> Any:
            raise frappe.ValidationError("forced cancellation controller failure")

        frappe.set_user(APPROVER)
        with patch.object(p2p_execution, "_apply_source_action", side_effect=failed_cancel):
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
        self.assertEqual(frappe.db.get_value("Purchase Order", po_name, "docstatus"), 1)
        frappe.set_user(BUYER)
        details = cast(dict[str, Any], get_run(proposal["run_id"]))
        self.assertTrue(details["ok"], details)
        governed = next(
            item
            for item in details["governance"]
            if item["action"]["action_id"] == proposal["action_id"]
        )
        receipt = governed["receipt"]
        self.assertEqual(receipt["final_state"], "FAILED")
        self.assertEqual(receipt["target_name"], po_name)
        evidence = receipt["reconciliation_evidence"]
        self.assertEqual(evidence["operation"], "cancel")
        self.assertEqual(evidence["side_effect_state"], "NOT_APPLIED")
        self.assertFalse(evidence["manual_intervention"])
        self.assertIn("repropose", evidence["recovery_action"])

    def test_cancel_response_loss_records_applied_side_effect_for_manual_recovery(self) -> None:
        po_name, pr_name, pi_name, _item_code, _pe_name = self._submitted_chain(payment=False)
        frappe.set_user("Administrator")
        frappe.get_doc("Purchase Invoice", pi_name).cancel()
        frappe.get_doc("Purchase Receipt", pr_name).cancel()
        frappe.db.commit()
        proposal = self._proposal("CANCEL_PO", "Purchase Order", po_name, initiator=BUYER)
        reviewed, _approved = self._approve(proposal, APPROVER)

        def committed_cancel(_action: Any, target: Any) -> Any:
            target.cancel()
            frappe.db.commit()
            raise RuntimeError("response lost after ERP cancellation commit")

        frappe.set_user(APPROVER)
        with patch.object(p2p_execution, "_apply_source_action", side_effect=committed_cancel):
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
        self.assertEqual(response["error"]["code"], "UNCERTAIN_RESULT")
        self.assertEqual(frappe.db.get_value("Purchase Order", po_name, "docstatus"), 2)
        frappe.set_user(BUYER)
        details = cast(dict[str, Any], get_run(proposal["run_id"]))
        governed = next(
            item
            for item in details["governance"]
            if item["action"]["action_id"] == proposal["action_id"]
        )
        receipt = governed["receipt"]
        self.assertEqual(receipt["final_state"], "RECONCILIATION_REQUIRED")
        evidence = receipt["reconciliation_evidence"]
        self.assertEqual(evidence["side_effect_state"], "APPLIED_BUT_RESPONSE_FAILED")
        self.assertTrue(evidence["manual_intervention"])
        self.assertIn("reconcile", evidence["recovery_action"])
