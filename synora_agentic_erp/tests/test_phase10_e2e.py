"""Real ERP evidence for the Phase 10 procure-to-pay lifecycle.

The test deliberately keeps every governed action in one Run.  Each native
ERP write is still a separate approved Action, so partial receipt, billing,
and settlement facts can be read back before the next action is proposed.
"""

from __future__ import annotations

import json
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
    finalize_p2p_run,
    get_run,
    issue_run,
    resume_p2p_run,
)
from synora_agentic_erp.governance import p2p_execution

E2E_OWNER = "synora-p10-e2e-owner@dev.localhost"
PO_APPROVER = "synora-p1-approver@dev.localhost"
RECEIPT_APPROVER = "synora-p1-receiver@dev.localhost"
INVOICE_APPROVER = "synora-p1-accountant@dev.localhost"
PAYMENT_APPROVER = "synora-p1-payment-approver@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
SUPPLIER = "SYNORA-P1-Supplier-1"
PRICE_LIST = "SYNORA-P1 Buying CNY"
ITEM_GROUP = "SYNORA-P1 Items"
STOCK_UOM = "Unit"
PAID_FROM = "Cash - SP1"
PAID_TO = "Creditors - SP1"


class TestPhase10RealP2PEndToEnd(FrappeTestCase):  # type: ignore[misc]
    """Exercise the full chain and the durable failure boundaries."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        frappe.set_user("Administrator")
        if not frappe.db.exists("User", E2E_OWNER):
            frappe.get_doc(
                {
                    "doctype": "User",
                    "email": E2E_OWNER,
                    "first_name": "Synora P10 E2E Owner",
                    "send_welcome_email": 0,
                    "enabled": 1,
                    "roles": [
                        {"role": "Purchase User"},
                        {"role": "Stock User"},
                        {"role": "Accounts User"},
                    ],
                }
            ).insert(ignore_permissions=True)
            frappe.db.commit()

    def tearDown(self) -> None:
        # This module is the Phase 10 real-ERP evidence batch.  Keep the
        # generated PO/PR/PI/Payment Entry history available for the final
        # Run-page/browser readback instead of cancelling it during teardown.
        frappe.set_user("Administrator")
        super().tearDown()

    def _draft_po(self) -> tuple[str, str, str]:
        item_code = f"SYNORA-P10-E2E-{uuid4().hex[:12]}"
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
        frappe.db.commit()
        return str(po.name), str(po.items[0].name), item_code

    def _new_run(self, goal: str) -> str:
        frappe.set_user(E2E_OWNER)
        issued = issue_run(
            COMPANY,
            goal,
            warehouse=WAREHOUSE,
            correlation_id=str(uuid4()),
        )
        self.assertTrue(issued["ok"], issued)
        run_id = str(issued["run"]["run_id"])
        analyzed = analyze_run(run_id, str(issued["correlation_id"]))
        self.assertTrue(analyzed["ok"], analyzed)
        self.assertEqual(analyzed["analysis"]["run_state"], "PROPOSED")
        return run_id

    def _proposal(
        self,
        run_id: str,
        action_type: str,
        source_doctype: str,
        source_name: str,
        *,
        source_row: str | None = None,
        item_code: str | None = None,
        quantity: int | None = None,
        paid_amount: int | None = None,
    ) -> dict[str, Any]:
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
                            "uom": STOCK_UOM,
                            "warehouse": WAREHOUSE,
                        }
                    ],
                }
            )
        elif action_type == "CREATE_PI_DRAFT":
            payload.update(
                {
                    "transaction_date": "2026-09-10",
                    "items": [
                        {
                            "source_row": source_row,
                            "item_code": item_code,
                            "qty": quantity,
                            "uom": STOCK_UOM,
                            "warehouse": WAREHOUSE,
                            "rate": 10,
                        }
                    ],
                }
            )
        elif action_type == "CREATE_PAYMENT_ENTRY_DRAFT":
            amount = paid_amount or 0
            payload.update(
                {
                    "party_type": "Supplier",
                    "party": SUPPLIER,
                    "payment_type": "Pay",
                    "posting_date": "2026-09-10",
                    "paid_from": PAID_FROM,
                    "paid_to": PAID_TO,
                    "paid_amount": amount,
                    "received_amount": amount,
                    "source_currency": "CNY",
                    "target_currency": "CNY",
                    "references": [
                        {
                            "reference_doctype": "Purchase Invoice",
                            "reference_name": source_name,
                            "allocated_amount": amount,
                        }
                    ],
                }
            )
        return {
            "schema_version": "2",
            "action_type": action_type,
            "run_id": run_id,
            "action_id": str(uuid4()),
            "initiator": E2E_OWNER,
            "payload": payload,
            "evidence_refs": [f"erp:{source_doctype}:{source_name}"],
            "calculation_refs": [f"p10-e2e:{source_name}"],
            "risk_class": "HIGH",
            "approval_class": "INDEPENDENT_APPROVER",
            "snapshot_ref": f"snapshot:{uuid4()}",
            "idempotency_key": f"p10-e2e-{action_type.lower()}-{uuid4().hex}",
            "expires_at": "2030-01-01T00:00:00Z",
            "revalidation_rule": "FULL_PRE_EXECUTE_RECHECK_P2P_V1",
            "summary": f"Phase 10 E2E {action_type} {source_name}",
            "correlation_id": str(uuid4()),
        }

    def _approve_and_execute(
        self, proposal: dict[str, Any], approver: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        frappe.set_user(E2E_OWNER)
        reviewed = cast(dict[str, Any], evaluate_proposal(proposal))
        self.assertTrue(reviewed["ok"], reviewed)
        frappe.set_user(approver)
        approved = cast(
            dict[str, Any],
            decide_action(
                proposal["action_id"],
                "ALLOW",
                reviewed["action"]["proposal_digest"],
                "independent Phase 10 E2E approval",
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
        return reviewed, response

    def test_full_p2p_lifecycle_stays_in_one_run_and_reconciles_accounts(self) -> None:
        po_name, po_item_name, item_code = self._draft_po()
        run_id = self._new_run(f"Phase 10 E2E P2P batch {po_name}")

        _po_review, po_response = self._approve_and_execute(
            self._proposal(run_id, "SUBMIT_PO", "Purchase Order", po_name),
            PO_APPROVER,
        )
        self.assertEqual(po_response["target"]["docstatus"], 1)
        self.assertEqual(po_response["receipt"]["verified_fields"]["source_name"], po_name)

        _first_pr_review, first_pr_draft = self._approve_and_execute(
            self._proposal(
                run_id,
                "CREATE_PR_DRAFT",
                "Purchase Order",
                po_name,
                source_row=po_item_name,
                item_code=item_code,
                quantity=1,
            ),
            RECEIPT_APPROVER,
        )
        first_pr_name = str(first_pr_draft["target"]["name"])
        self.assertEqual(first_pr_draft["target"]["docstatus"], 0)
        self.assertEqual(first_pr_draft["receipt"]["verified_fields"]["item_0.qty"], "1")
        _first_pr_submit_review, first_pr_response = self._approve_and_execute(
            self._proposal(run_id, "SUBMIT_PR", "Purchase Receipt", first_pr_name),
            RECEIPT_APPROVER,
        )
        self.assertEqual(first_pr_response["target"]["docstatus"], 1)
        self.assertEqual(
            frappe.db.get_value("Purchase Order Item", po_item_name, "received_qty"), 1
        )

        _second_pr_review, second_pr_draft = self._approve_and_execute(
            self._proposal(
                run_id,
                "CREATE_PR_DRAFT",
                "Purchase Order",
                po_name,
                source_row=po_item_name,
                item_code=item_code,
                quantity=1,
            ),
            RECEIPT_APPROVER,
        )
        second_pr_name = str(second_pr_draft["target"]["name"])
        _second_pr_submit_review, second_pr_response = self._approve_and_execute(
            self._proposal(run_id, "SUBMIT_PR", "Purchase Receipt", second_pr_name),
            RECEIPT_APPROVER,
        )
        self.assertEqual(second_pr_response["target"]["docstatus"], 1)
        self.assertEqual(
            frappe.db.get_value("Purchase Order Item", po_item_name, "received_qty"), 2
        )

        _first_pi_review, first_pi_draft = self._approve_and_execute(
            self._proposal(
                run_id,
                "CREATE_PI_DRAFT",
                "Purchase Receipt",
                first_pr_name,
                source_row=str(
                    frappe.db.get_value("Purchase Receipt Item", {"parent": first_pr_name}, "name")
                ),
                item_code=item_code,
                quantity=1,
            ),
            INVOICE_APPROVER,
        )
        first_pi_name = str(first_pi_draft["target"]["name"])
        _first_pi_submit_review, first_pi_response = self._approve_and_execute(
            self._proposal(run_id, "SUBMIT_PI", "Purchase Invoice", first_pi_name),
            INVOICE_APPROVER,
        )
        self.assertEqual(first_pi_response["target"]["docstatus"], 1)
        self.assertEqual(
            first_pi_response["receipt"]["verified_fields"]["outstanding_amount"], "10"
        )

        second_pr_item_name = str(
            frappe.db.get_value("Purchase Receipt Item", {"parent": second_pr_name}, "name")
        )
        _second_pi_review, second_pi_draft = self._approve_and_execute(
            self._proposal(
                run_id,
                "CREATE_PI_DRAFT",
                "Purchase Receipt",
                second_pr_name,
                source_row=second_pr_item_name,
                item_code=item_code,
                quantity=1,
            ),
            INVOICE_APPROVER,
        )
        second_pi_name = str(second_pi_draft["target"]["name"])
        _second_pi_submit_review, second_pi_response = self._approve_and_execute(
            self._proposal(run_id, "SUBMIT_PI", "Purchase Invoice", second_pi_name),
            INVOICE_APPROVER,
        )
        self.assertEqual(
            second_pi_response["receipt"]["verified_fields"]["outstanding_amount"], "10"
        )
        self.assertEqual(frappe.db.get_value("Purchase Order", po_name, "per_billed"), 100.0)

        _first_payment_review, first_payment_draft = self._approve_and_execute(
            self._proposal(
                run_id,
                "CREATE_PAYMENT_ENTRY_DRAFT",
                "Purchase Invoice",
                first_pi_name,
                paid_amount=5,
            ),
            INVOICE_APPROVER,
        )
        first_payment_name = str(first_payment_draft["target"]["name"])
        _first_payment_submit_review, first_payment_response = self._approve_and_execute(
            self._proposal(
                run_id,
                "SUBMIT_PAYMENT_ENTRY",
                "Payment Entry",
                first_payment_name,
            ),
            PAYMENT_APPROVER,
        )
        self.assertEqual(
            first_payment_response["receipt"]["verified_fields"]["purchase_invoice_status"],
            "Partly Paid",
        )
        self.assertEqual(
            first_payment_response["receipt"]["verified_fields"][
                "purchase_invoice_outstanding_amount"
            ],
            "5",
        )

        _second_payment_review, second_payment_draft = self._approve_and_execute(
            self._proposal(
                run_id,
                "CREATE_PAYMENT_ENTRY_DRAFT",
                "Purchase Invoice",
                first_pi_name,
                paid_amount=5,
            ),
            INVOICE_APPROVER,
        )
        second_payment_name = str(second_payment_draft["target"]["name"])
        _second_payment_submit_review, second_payment_response = self._approve_and_execute(
            self._proposal(
                run_id,
                "SUBMIT_PAYMENT_ENTRY",
                "Payment Entry",
                second_payment_name,
            ),
            PAYMENT_APPROVER,
        )
        self.assertEqual(
            second_payment_response["receipt"]["verified_fields"]["purchase_invoice_status"],
            "Paid",
        )

        _third_payment_review, third_payment_draft = self._approve_and_execute(
            self._proposal(
                run_id,
                "CREATE_PAYMENT_ENTRY_DRAFT",
                "Purchase Invoice",
                second_pi_name,
                paid_amount=10,
            ),
            INVOICE_APPROVER,
        )
        third_payment_name = str(third_payment_draft["target"]["name"])
        _third_payment_submit_review, third_payment_response = self._approve_and_execute(
            self._proposal(
                run_id,
                "SUBMIT_PAYMENT_ENTRY",
                "Payment Entry",
                third_payment_name,
            ),
            PAYMENT_APPROVER,
        )
        self.assertEqual(
            third_payment_response["receipt"]["verified_fields"]["purchase_invoice_status"],
            "Paid",
        )

        frappe.set_user(E2E_OWNER)
        before_finalize = cast(dict[str, Any], get_run(run_id))
        self.assertTrue(before_finalize["ok"], before_finalize)
        chain = before_finalize["p2p_chain"]
        self.assertTrue(chain["completion_ready"], chain)
        # One invoice is paid in two installments, so the lifecycle has
        # fifteen governed actions: PO submit (1), two receipt pairs (4),
        # two invoice pairs (4), and three payment pairs (6).
        self.assertEqual(len(chain["steps"]), 15)
        self.assertTrue(all(step["state"] == "SUCCEEDED" for step in chain["steps"]), chain)
        self.assertEqual(before_finalize["run"]["run_state"], "EXECUTING")

        finalized = cast(dict[str, Any], finalize_p2p_run(run_id, str(uuid4())))
        self.assertTrue(finalized["ok"], finalized)
        self.assertEqual(finalized["run"]["run_state"], "SUCCEEDED")
        self.assertTrue(finalized["run"]["chain"]["completion_ready"])
        self.assertEqual(frappe.db.get_value("Purchase Invoice", first_pi_name, "status"), "Paid")
        self.assertEqual(frappe.db.get_value("Purchase Invoice", second_pi_name, "status"), "Paid")
        self.assertGreater(
            frappe.db.count(
                "GL Entry",
                {
                    "voucher_type": "Payment Entry",
                    "voucher_no": [
                        "in",
                        [first_payment_name, second_payment_name, third_payment_name],
                    ],
                },
            ),
            0,
        )

        # Emit only safe identifiers for the host-side evidence recorder.
        print(
            "PHASE10_E2E_RESULT "
            + json.dumps(
                {
                    "run_id_prefix": run_id[:12],
                    "po_name": po_name,
                    "receipt_names": [first_pr_name, second_pr_name],
                    "invoice_names": [first_pi_name, second_pi_name],
                    "payment_entry_names": [
                        first_payment_name,
                        second_payment_name,
                        third_payment_name,
                    ],
                    "action_count": len(chain["steps"]),
                    "run_state": finalized["run"]["run_state"],
                    "po_per_received": frappe.db.get_value(
                        "Purchase Order", po_name, "per_received"
                    ),
                    "po_per_billed": frappe.db.get_value("Purchase Order", po_name, "per_billed"),
                    "invoice_statuses": [
                        frappe.db.get_value("Purchase Invoice", first_pi_name, "status"),
                        frappe.db.get_value("Purchase Invoice", second_pi_name, "status"),
                    ],
                },
                sort_keys=True,
            )
        )

    def test_response_loss_replays_without_duplicate_and_blocks_unknown_downstream(self) -> None:
        po_name, po_item_name, item_code = self._draft_po()
        run_id = self._new_run(f"Phase 10 E2E response loss {po_name}")
        proposal = self._proposal(
            run_id,
            "SUBMIT_PO",
            "Purchase Order",
            po_name,
        )
        frappe.set_user(E2E_OWNER)
        reviewed = cast(dict[str, Any], evaluate_proposal(proposal))
        self.assertTrue(reviewed["ok"], reviewed)
        frappe.set_user(PO_APPROVER)
        approved = decide_action(
            proposal["action_id"],
            "ALLOW",
            reviewed["action"]["proposal_digest"],
            "response-loss approval",
            str(uuid4()),
        )
        self.assertTrue(approved["ok"], approved)

        with patch.object(
            p2p_execution,
            "_success_response",
            side_effect=RuntimeError("simulated response delivery loss"),
        ):
            lost = cast(
                dict[str, Any],
                execute_p2p_action(
                    proposal["action_id"],
                    reviewed["action"]["proposal_digest"],
                    proposal["idempotency_key"],
                    str(uuid4()),
                ),
            )
        self.assertFalse(lost["ok"])
        self.assertEqual(lost["error"]["code"], "UNCERTAIN_RESULT")
        self.assertEqual(frappe.db.get_value("Purchase Order", po_name, "docstatus"), 1)
        reservation = frappe.get_last_doc(
            "Synora Execution Reservation", filters={"action": proposal["action_id"]}
        )
        receipt = frappe.get_doc("Synora Execution Receipt", reservation.receipt)
        self.assertEqual(reservation.status, "SUCCEEDED")
        self.assertEqual(receipt.final_state, "SUCCEEDED")

        frappe.set_user(PO_APPROVER)
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
        self.assertEqual(frappe.db.count("Purchase Order", {"name": po_name}), 1)

        # A new downstream action still needs its own approval; the replay did
        # not manufacture a future receipt or silently close the Run.
        downstream = self._proposal(
            run_id,
            "CREATE_PR_DRAFT",
            "Purchase Order",
            po_name,
            source_row=po_item_name,
            item_code=item_code,
            quantity=1,
        )
        frappe.set_user(E2E_OWNER)
        downstream_review = cast(dict[str, Any], evaluate_proposal(downstream))
        self.assertTrue(downstream_review["ok"], downstream_review)
        self.assertEqual(downstream_review["action"]["state"], "AWAITING_APPROVAL")
        self.assertEqual(
            frappe.db.get_value(
                "Synora P2P Plan Step", {"action": downstream["action_id"]}, "state"
            ),
            "WAITING_APPROVAL",
        )
        frappe.set_user(E2E_OWNER)
        details = cast(dict[str, Any], get_run(run_id))
        self.assertEqual(details["run"]["run_state"], "EXECUTING")
        self.assertFalse(details["p2p_chain"]["completion_ready"])

    def test_committed_submit_without_receipt_freezes_run_for_manual_reconciliation(self) -> None:
        po_name, _po_item_name, _item_code = self._draft_po()
        run_id = self._new_run(f"Phase 10 E2E unknown result {po_name}")
        proposal = self._proposal(run_id, "SUBMIT_PO", "Purchase Order", po_name)
        frappe.set_user(E2E_OWNER)
        reviewed = cast(dict[str, Any], evaluate_proposal(proposal))
        self.assertTrue(reviewed["ok"], reviewed)
        frappe.set_user(PO_APPROVER)
        approved = decide_action(
            proposal["action_id"],
            "ALLOW",
            reviewed["action"]["proposal_digest"],
            "unknown-result approval",
            str(uuid4()),
        )
        self.assertTrue(approved["ok"], approved)

        def committed_then_lost(_action: Any, target: Any) -> Any:
            target.submit()
            frappe.db.commit()
            raise RuntimeError("simulated loss after native submit commit")

        frappe.set_user(PO_APPROVER)
        with patch.object(p2p_execution, "_apply_source_action", side_effect=committed_then_lost):
            lost = cast(
                dict[str, Any],
                execute_p2p_action(
                    proposal["action_id"],
                    reviewed["action"]["proposal_digest"],
                    proposal["idempotency_key"],
                    str(uuid4()),
                ),
            )
        self.assertFalse(lost["ok"])
        self.assertEqual(lost["error"]["code"], "UNCERTAIN_RESULT")
        self.assertEqual(frappe.db.get_value("Purchase Order", po_name, "docstatus"), 1)
        frappe.set_user(E2E_OWNER)
        details = cast(dict[str, Any], get_run(run_id))
        receipt = next(
            row["receipt"]
            for row in details["governance"]
            if row["action"]["action_id"] == proposal["action_id"]
        )
        self.assertEqual(receipt["final_state"], "RECONCILIATION_REQUIRED")
        self.assertEqual(
            receipt["reconciliation_evidence"]["side_effect_state"],
            "APPLIED_BUT_RESPONSE_FAILED",
        )
        self.assertEqual(details["run"]["run_state"], "RECONCILIATION_REQUIRED")
        self.assertEqual(details["p2p_chain"]["status"], "RECONCILIATION_REQUIRED")

        frappe.set_user(E2E_OWNER)
        resumed = cast(dict[str, Any], resume_p2p_run(run_id, str(uuid4())))
        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual(resumed["run"]["run_state"], "RECONCILIATION_REQUIRED")
        self.assertTrue(resumed["run"]["chain"]["needs_reinvestigation"] is False)
