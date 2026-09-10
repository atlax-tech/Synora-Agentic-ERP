"""Pure projection tests for the durable Phase 10 P2P orchestration chain."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import frappe
from frappe.tests.utils import FrappeTestCase

from synora_agentic_erp.api import cancel_p2p_run, finalize_p2p_run, issue_run, resume_p2p_run
from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.governance.p2p_orchestration import (
    chain_from_entries,
    derive_step_views,
    infer_dependencies,
)
from synora_agentic_erp.governance.policy import _run_identity

COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
BUYER = "synora-p1-buyer@dev.localhost"


def _action(action_type: str, action_id: str, source_name: str = "PO-1") -> SimpleNamespace:
    return SimpleNamespace(
        action_type=action_type,
        action_id=action_id,
        run_id="run-1",
        correlation_id="corr-1",
        payload={
            "source_doctype": "Purchase Order",
            "source_name": source_name,
        },
    )


def _entry(
    action_type: str,
    action_id: str,
    *,
    state: str,
    receipt_state: str | None = None,
    source_name: str = "PO-1",
    created_at: str,
) -> dict[str, object]:
    receipt = (
        SimpleNamespace(
            final_state=receipt_state,
            target_doctype="Purchase Order",
            target_name=source_name,
            verified_fields_json="{}",
        )
        if receipt_state
        else None
    )
    action = _action(action_type, action_id, source_name)
    return {
        "action": action,
        "action_id": action_id,
        "action_type": action_type,
        "run_id": "run-1",
        "state": state,
        "state_reason": "",
        "receipt": receipt,
        "target_ref": ("Purchase Order", source_name),
        "related_refs": {("Purchase Order", source_name)},
        "created_at": created_at,
    }


class TestPhase10OrchestrationProjection(FrappeTestCase):  # type: ignore[misc]
    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        super().tearDown()

    def test_dependencies_follow_source_chain_and_keep_partial_steps_visible(self) -> None:
        entries = [
            _entry(
                "SUBMIT_PO", "a-po", state="EXECUTED", receipt_state="SUCCEEDED", created_at="1"
            ),
            _entry(
                "CREATE_PR_DRAFT",
                "b-pr-draft",
                state="APPROVED",
                source_name="PO-1",
                created_at="2",
            ),
            _entry(
                "CREATE_PR_DRAFT",
                "c-pr-draft",
                state="AWAITING_APPROVAL",
                source_name="PO-1",
                created_at="3",
            ),
        ]

        dependencies = infer_dependencies(entries)
        self.assertEqual(dependencies["b-pr-draft"], ("a-po",))
        self.assertEqual(dependencies["c-pr-draft"], ("a-po",))

        views = {view.action_id: view for view in derive_step_views(entries)}
        self.assertEqual(views["b-pr-draft"].state, "READY")
        self.assertEqual(views["c-pr-draft"].state, "WAITING_APPROVAL")

    def test_failed_or_uncertain_predecessor_blocks_downstream_action(self) -> None:
        entries = [
            _entry("SUBMIT_PO", "a-po", state="EXECUTED", receipt_state="FAILED", created_at="1"),
            _entry("CREATE_PR_DRAFT", "b-pr", state="APPROVED", created_at="2"),
        ]

        chain = chain_from_entries("EXECUTING", entries)
        self.assertEqual(chain["status"], "BLOCKED")
        self.assertFalse(chain["completion_ready"])
        self.assertIn("dependency a-po is FAILED", chain["blocked_reasons"])
        self.assertEqual(chain["steps"][1]["state"], "BLOCKED")

    def test_executed_without_success_receipt_requires_reconciliation(self) -> None:
        entry = _entry("SUBMIT_PO", "a-po", state="EXECUTED", created_at="1")

        chain = chain_from_entries("EXECUTING", [entry])
        self.assertEqual(chain["status"], "RECONCILIATION_REQUIRED")
        self.assertFalse(chain["completion_ready"])
        self.assertEqual(chain["steps"][0]["state"], "RECONCILIATION_REQUIRED")

    def test_started_reservation_is_executing_until_receipt_is_saved(self) -> None:
        entry = _entry("SUBMIT_PO", "a-po", state="APPROVED", created_at="1")
        entry["reservation"] = {"status": "STARTED"}

        views = derive_step_views([entry])
        self.assertEqual(views[0].state, "EXECUTING")

    def test_declined_action_projects_to_supported_failed_plan_step(self) -> None:
        entries = [
            _entry("SUBMIT_PO", "a-po", state="DECLINED", created_at="1"),
            _entry("CREATE_PR_DRAFT", "b-pr", state="APPROVED", created_at="2"),
        ]

        chain = chain_from_entries("EXECUTING", entries)
        self.assertEqual(chain["steps"][0]["state"], "FAILED")
        self.assertEqual(chain["steps"][1]["state"], "BLOCKED")
        self.assertIn("dependency a-po is FAILED", chain["blocked_reasons"])

    def test_plan_execute_uses_workflow_deadline_after_segment_capability_expires(self) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        run = SimpleNamespace(
            initiator=BUYER,
            status="ACTIVE",
            revoked=0,
            run_state="AWAITING_APPROVAL",
            execution_mode="PLAN_EXECUTE",
            expires_at=now.replace(year=2020),
            workflow_expires_at=now.replace(year=2030),
        )
        action = SimpleNamespace(initiator=BUYER)
        result = _run_identity(action, run, BUYER)
        self.assertEqual(result.status, "PASS")

    def test_verified_receipts_alone_cannot_close_without_a_business_goal(self) -> None:
        entries = [
            _entry(
                "SUBMIT_PO", "a-po", state="EXECUTED", receipt_state="SUCCEEDED", created_at="1"
            ),
            _entry(
                "CREATE_PR_DRAFT",
                "b-pr",
                state="EXECUTED",
                receipt_state="RECONCILED_SUCCESS",
                created_at="2",
            ),
        ]

        chain = chain_from_entries("EXECUTING", entries)
        self.assertEqual(chain["status"], "WAITING_GOAL_CONFIRMATION")
        self.assertFalse(chain["completion_ready"])
        self.assertIsNone(chain["next_step_id"])

    def test_normalize_p2p_goal_canonicalizes_decimal_targets(self) -> None:
        from synora_agentic_erp.governance.p2p_orchestration import normalize_p2p_goal

        goal = normalize_p2p_goal(
            {
                "source_doctype": "Purchase Order",
                "source_name": "PO-1",
                "source_rows": [
                    {"source_row": "row-1", "item_code": "ITEM-1", "target_qty": "2.00"}
                ],
            }
        )
        self.assertEqual(goal["schema_version"], "1")
        self.assertEqual(goal["source_rows"][0]["target_qty"], "2")
        self.assertEqual(goal["settlement_endpoint"], "RECEIVED_BILLED_PAID")
        with self.assertRaises(GatewayFault):
            normalize_p2p_goal(
                {
                    "source_doctype": "Purchase Order",
                    "source_name": "PO-1",
                    "source_rows": [
                        {
                            "source_row": "row-1",
                            "item_code": "ITEM-1",
                            "target_qty": "2",
                            "target_amount": "1",
                        }
                    ],
                }
            )

    def test_business_progress_stays_open_after_all_actions_have_receipts(self) -> None:
        entries = [
            _entry("SUBMIT_PO", "a-po", state="EXECUTED", receipt_state="SUCCEEDED", created_at="1")
        ]
        chain = chain_from_entries(
            "EXECUTING",
            entries,
            goal={
                "source_doctype": "Purchase Order",
                "source_name": "PO-1",
                "source_rows": [{"source_row": "row-1", "item_code": "ITEM-1", "target_qty": "2"}],
            },
            business_progress={
                "goal_state": "CONFIRMED",
                "goal_version": 1,
                "goal_digest": "digest",
                "complete": False,
                "target_qty": "2",
                "received_qty": "1",
                "remaining_qty": "1",
                "target_amount": "20",
                "billed_amount": "10",
                "outstanding_amount": "10",
                "blocked_reasons": ["target still has one unit outstanding"],
            },
        )
        self.assertEqual(chain["status"], "WAITING_BUSINESS_FACTS")
        self.assertFalse(chain["completion_ready"])
        self.assertIn("target still has one unit outstanding", chain["blocked_reasons"])

    def test_stale_goal_requires_reinvestigation_even_when_steps_succeeded(self) -> None:
        entry = _entry(
            "SUBMIT_PO", "a-po", state="EXECUTED", receipt_state="SUCCEEDED", created_at="1"
        )
        chain = chain_from_entries(
            "EXECUTING",
            [entry],
            goal={"source_name": "PO-1"},
            business_progress={
                "goal_state": "STALE",
                "goal_version": 1,
                "goal_digest": "digest",
                "complete": True,
                "blocked_reasons": ["source Purchase Order changed"],
            },
        )
        self.assertEqual(chain["status"], "REINVESTIGATION_REQUIRED")
        self.assertFalse(chain["completion_ready"])

    def test_terminal_run_state_is_preserved_when_projection_has_no_steps(self) -> None:
        cancelled = chain_from_entries("CANCELLED", [])
        expired = chain_from_entries("EXPIRED", [])

        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertEqual(expired["status"], "EXPIRED")
        self.assertFalse(cancelled["completion_ready"])

    def test_run_controls_are_durable_and_finalize_requires_a_verified_step(self) -> None:
        frappe.set_user(BUYER)
        issued = issue_run(
            COMPANY,
            "phase10 orchestration controls",
            warehouse=WAREHOUSE,
            correlation_id=str(uuid4()),
        )
        self.assertTrue(issued["ok"], issued)
        run_id = str(issued["run"]["run_id"])

        resumed = resume_p2p_run(run_id, str(uuid4()))
        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual(resumed["run"]["chain"]["steps"], [])
        self.assertEqual(
            finalize_p2p_run(run_id, str(uuid4()))["error"]["code"],
            "CONFLICT",
        )

        cancelled = cancel_p2p_run(run_id, str(uuid4()))
        self.assertTrue(cancelled["ok"], cancelled)
        self.assertEqual(cancelled["run"]["run_state"], "CANCELLED")
