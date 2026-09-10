"""Real Frappe acceptance for the Phase 10 PO Submit action."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import frappe
from frappe.tests.utils import FrappeTestCase

from synora_agentic_erp.api import (
    analyze_run,
    decide_action,
    evaluate_proposal,
    execute_p2p_action,
    get_governed_action,
    get_run,
    issue_run,
    list_pending_approvals,
)

BUYER = "synora-p1-buyer@dev.localhost"
APPROVER = "synora-p1-approver@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
SUPPLIER = "SYNORA-P1-Supplier-1"
PRICE_LIST = "SYNORA-P1 Buying CNY"
ITEM_GROUP = "SYNORA-P1 Items"
STOCK_UOM = "Unit"


class TestPhase10PurchaseOrderSubmit(FrappeTestCase):  # type: ignore[misc]
    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        super().tearDown()

    def _draft_po(self) -> str:
        item_code = f"SYNORA-P10-PO-{uuid4().hex[:12]}"
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
        return str(po.name)

    def _proposal(self, po_name: str) -> dict[str, Any]:
        frappe.set_user(BUYER)
        issued = issue_run(
            COMPANY,
            f"submit purchase order {po_name}",
            warehouse=WAREHOUSE,
            correlation_id=str(uuid4()),
        )
        self.assertTrue(issued["ok"], issued)
        run = issued["run"]
        analyzed = analyze_run(str(run["run_id"]), str(issued["correlation_id"]))
        self.assertTrue(analyzed["ok"], analyzed)
        return {
            "schema_version": "2",
            "action_type": "SUBMIT_PO",
            "run_id": str(run["run_id"]),
            "action_id": str(uuid4()),
            "initiator": BUYER,
            "payload": {
                "company": COMPANY,
                "source_doctype": "Purchase Order",
                "source_name": po_name,
            },
            "evidence_refs": [f"erp:{po_name}"],
            "calculation_refs": [f"state:{po_name}"],
            "risk_class": "HIGH",
            "approval_class": "INDEPENDENT_APPROVER",
            "snapshot_ref": f"snapshot:{uuid4()}",
            "idempotency_key": f"p10-submit-{uuid4().hex}",
            "expires_at": "2030-01-01T00:00:00Z",
            "revalidation_rule": "FULL_PRE_EXECUTE_RECHECK_P2P_V1",
            "summary": "Submit one Purchase Order",
            "correlation_id": str(uuid4()),
        }

    def test_submit_requires_independent_approver_and_updates_erp(self) -> None:
        po_name = self._draft_po()
        proposal = self._proposal(po_name)
        reviewed = cast(dict[str, Any], evaluate_proposal(proposal))
        self.assertTrue(reviewed["ok"], reviewed)
        self.assertEqual(reviewed["action"]["state"], "AWAITING_APPROVAL")

        frappe.set_user(APPROVER)
        pending = cast(dict[str, Any], list_pending_approvals())
        self.assertTrue(pending["ok"], pending)
        self.assertIn(proposal["action_id"], {item["action_id"] for item in pending["approvals"]})
        detail = cast(dict[str, Any], get_governed_action(proposal["action_id"]))
        self.assertTrue(detail["ok"], detail)
        self.assertEqual(detail["action"]["action_type"], "SUBMIT_PO")
        self.assertEqual(get_run(proposal["run_id"])["error"]["code"], "RUN_REJECTED")

        frappe.set_user(BUYER)
        self.assertEqual(
            decide_action(
                proposal["action_id"],
                "ALLOW",
                reviewed["action"]["proposal_digest"],
                "buyer cannot self approve",
                str(uuid4()),
            )["error"]["code"],
            "PERMISSION_DENIED",
        )

        frappe.set_user(APPROVER)
        approved = cast(
            dict[str, Any],
            decide_action(
                proposal["action_id"],
                "ALLOW",
                reviewed["action"]["proposal_digest"],
                "independent approval",
                str(uuid4()),
            ),
        )
        self.assertTrue(approved["ok"], approved)
        approved_queue = cast(dict[str, Any], list_pending_approvals())
        self.assertTrue(approved_queue["ok"], approved_queue)
        approved_row = next(
            item
            for item in approved_queue["approvals"]
            if item["action_id"] == proposal["action_id"]
        )
        self.assertEqual(approved_row["state"], "APPROVED")

        frappe.set_user(BUYER)
        denied_execution = cast(
            dict[str, Any],
            execute_p2p_action(
                proposal["action_id"],
                reviewed["action"]["proposal_digest"],
                proposal["idempotency_key"],
                str(uuid4()),
            ),
        )
        self.assertEqual(denied_execution["error"]["code"], "PERMISSION_DENIED")

        frappe.set_user(APPROVER)
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
        self.assertEqual(response["action"]["state"], "EXECUTED")
        self.assertEqual(response["target"]["doctype"], "Purchase Order")
        self.assertEqual(response["target"]["name"], po_name)
        self.assertEqual(response["target"]["docstatus"], 1)
        self.assertEqual(response["receipt"]["approver"], APPROVER)
        self.assertEqual(response["run"]["run_state"], "EXECUTING")

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

        frappe.set_user(BUYER)
        run_details = cast(dict[str, Any], get_run(proposal["run_id"]))
        self.assertTrue(run_details["ok"], run_details)
        governed = next(
            item
            for item in run_details["governance"]
            if item["action"]["action_id"] == proposal["action_id"]
        )
        self.assertEqual(governed["receipt"]["target_name"], po_name)

        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.get_value("Purchase Order", po_name, "docstatus"), 1)
        self.assertEqual(
            frappe.db.get_value("Synora Proposed Action", proposal["action_id"], "state"),
            "EXECUTED",
        )
