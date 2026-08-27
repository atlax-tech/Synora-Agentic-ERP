"""Phase 5 Frappe contract, expiry and Gateway invocation-ledger tests."""

import hashlib
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from synora_agentic_erp.agent.invocation import args_digest, invocation_id
from synora_agentic_erp.api import (
    analyze_run,
    cancel_run,
    execute,
    get_run_workflow,
    issue_run,
    resume_run,
)
from synora_agentic_erp.gateway.security import resolve_run

BUYER = "synora-p1-buyer@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
GOAL = "ensure stock for SYNORA-P1-Item-1001 for the next quarter"


class TestWorkflowRun(FrappeTestCase):
    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        super().tearDown()

    def _issue(self, *, mode: str = "PLAN_EXECUTE") -> dict[str, object]:
        frappe.set_user(BUYER)
        response = issue_run(
            COMPANY,
            GOAL,
            correlation_id=str(uuid4()),
            execution_mode=mode,
        )
        self.assertTrue(response["ok"])
        return response["run"]

    @staticmethod
    def _workflow_response(
        run_id: str,
        *,
        status: str,
        revision: int,
        interrupt_id: str | None = None,
        resumed: bool = False,
    ) -> dict[str, object]:
        deadline = (now_datetime() + timedelta(hours=23)).isoformat()
        digest = hashlib.sha256(b"accepted clarification").hexdigest()
        step_clarification = {
            "schema_version": "1",
            "interrupt_id": interrupt_id or str(uuid4()),
            "question": "Which warehouse?",
            "answer_type": "TEXT",
            "answer_max_length": 40,
            "choices": [],
        }
        clarification = (
            {
                **step_clarification,
            }
            if interrupt_id
            else None
        )
        waiting = {
            "schema_version": "1",
            "step_id": "ask-warehouse",
            "order": 1,
            "type": "CLARIFICATION",
            "depends_on": [],
            "allowed_tools": [],
            "tool_name": None,
            "clarification": step_clarification,
            "parameters": {},
            "status": "WAITING" if status == "INTERRUPTED" else "SUCCEEDED",
            "observation_digest": None if status == "INTERRUPTED" else digest,
            "error": None,
            "completed_at": None if status == "INTERRUPTED" else deadline,
        }
        tool = {
            "schema_version": "1",
            "step_id": "read-mr",
            "order": 2,
            "type": "TOOL",
            "depends_on": ["ask-warehouse"],
            "allowed_tools": ["material_request.open"],
            "tool_name": "material_request.open",
            "clarification": None,
            "parameters": {"offset": 0, "limit": 20},
            "status": "PENDING" if status == "INTERRUPTED" else "SUCCEEDED",
            "observation_digest": None if status == "INTERRUPTED" else digest,
            "error": None,
            "completed_at": None if status == "INTERRUPTED" else deadline,
        }
        state = {
            "schema_version": "1",
            "run_id": run_id,
            "revision": revision,
            "plan_version": 1,
            "graph_version": "workflow-v1",
            "status": status,
            "current_step_id": "ask-warehouse" if status == "INTERRUPTED" else None,
            "steps": [waiting, tool],
            "clarification": clarification,
            "replan_reason": "INPUT_CLARIFIED" if resumed else None,
            "budget": {"max_steps": 64, "max_elapsed_ms": 300000, "max_observation_bytes": 4000},
            "deadline": deadline,
            "trace_id": str(uuid4()),
            "stop_reason": "workflow completed" if status == "SUCCEEDED" else None,
            "crash_recovered": False,
        }
        return {
            "schema_version": "1",
            "result": {
                "schema_version": "1",
                "state": state,
                "observations": [],
                "resumed": resumed,
            },
        }

    def test_plan_execute_issue_separates_workflow_deadline_and_capability(self) -> None:
        result = self._issue()
        self.assertNotIn("capability", result)
        self.assertIsNotNone(result["workflow_expires_at"])
        run = frappe.get_doc("Synora Agent Run", result["run_id"])
        self.assertEqual(run.execution_mode, "PLAN_EXECUTE")
        self.assertGreater(run.workflow_expires_at, run.expires_at)

    def test_expired_plan_run_is_authoritatively_closed(self) -> None:
        result = self._issue()
        frappe.db.set_value(
            "Synora Agent Run",
            result["run_id"],
            "workflow_expires_at",
            now_datetime() - timedelta(seconds=1),
        )
        response = analyze_run(str(result["run_id"]), str(uuid4()))
        self.assertEqual(response["error"]["code"], "CONFLICT")
        run = frappe.get_doc("Synora Agent Run", result["run_id"])
        self.assertEqual(run.run_state, "EXPIRED")
        self.assertEqual(run.status, "EXPIRED")
        self.assertEqual(run.revoked, 1)

    def test_cancel_expired_plan_run_prefers_expiry(self) -> None:
        result = self._issue()
        frappe.db.set_value(
            "Synora Agent Run",
            result["run_id"],
            "workflow_expires_at",
            now_datetime() - timedelta(seconds=1),
        )
        response = cancel_run(str(result["run_id"]), str(uuid4()))
        self.assertTrue(response["ok"])
        self.assertEqual(response["run"]["run_state"], "EXPIRED")

    def test_plan_workflow_interrupt_resume_and_status_are_revision_bound(self) -> None:
        run = self._issue()
        run_id = str(run["run_id"])
        interrupt_id = str(uuid4())
        interrupted = self._workflow_response(
            run_id,
            status="INTERRUPTED",
            revision=3,
            interrupt_id=interrupt_id,
        )
        completed = self._workflow_response(
            run_id,
            status="SUCCEEDED",
            revision=5,
            resumed=True,
        )
        with patch(
            "synora_agentic_erp.agent.service._call_workflow_runtime",
            side_effect=[interrupted, interrupted, completed],
        ):
            frappe.set_user(BUYER)
            started = analyze_run(run_id, str(uuid4()))
            self.assertTrue(started["ok"])
            self.assertEqual(started["analysis"]["workflow_status"], "INTERRUPTED")
            stored = frappe.get_doc("Synora Agent Run", run_id)
            self.assertEqual(stored.run_state, "ANALYZING")
            status = get_run_workflow(run_id)
            self.assertTrue(status["ok"])
            resumed = resume_run(
                run_id,
                str(uuid4()),
                workflow_revision=3,
                interrupt_id=interrupt_id,
                answer="Stores",
            )
        self.assertTrue(resumed["ok"])
        self.assertEqual(resumed["analysis"]["workflow_status"], "SUCCEEDED")
        self.assertEqual(
            frappe.get_doc("Synora Agent Run", run_id).run_state,
            "PROPOSED",
        )
        trace_types = frappe.get_all(
            "Synora Workflow Trace",
            filters={"run": run_id},
            pluck="event_type",
            order_by="sequence asc",
        )
        self.assertIn("INTERRUPTED", trace_types)
        self.assertIn("RESUMED", trace_types)

    def test_workflow_status_hides_another_users_run(self) -> None:
        run = self._issue()
        frappe.set_user("synora-p1-accountant@dev.localhost")
        response = get_run_workflow(str(run["run_id"]))
        self.assertEqual(response["error"]["code"], "RUN_REJECTED")

    def test_completed_workflow_invocation_is_cached_without_replay(self) -> None:
        result = self._issue(mode="DETERMINISTIC")
        run_id = str(result["run_id"])
        capability = str(result["capability"])
        correlation_id = str(uuid4())
        tool_input = {"query": "SYNORA-P1", "limit": 1, "offset": 0}
        digest = args_digest(tool_input)
        key = invocation_id(run_id, 1, "read-item", "item.lookup", "1", digest)
        payload = {
            "schema_version": "1",
            "run_id": run_id,
            "capability": capability,
            "correlation_id": correlation_id,
            "tool": {"name": "item.lookup", "version": "1", "input": tool_input},
            "invocation_id": key,
            "plan_version": 1,
            "step_id": "read-item",
            "args_digest": digest,
        }
        frappe.set_user("Guest")
        first = execute(**payload)
        self.assertTrue(first["ok"])
        second = execute(**payload)
        self.assertEqual(second, first)
        frappe.set_user("Administrator")
        ledger = frappe.get_all(
            "Synora Workflow Tool Invocation",
            filters={"invocation_id": key},
            fields=["status", "observation_digest"],
        )
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0].status, "SUCCEEDED")
        self.assertTrue(ledger[0].observation_digest)
        audits = frappe.get_all(
            "Synora Gateway Audit",
            filters={"run": run_id, "correlation_id": correlation_id},
            fields=["outcome"],
            order_by="creation desc",
        )
        self.assertEqual([entry.outcome for entry in audits[:2]], ["CACHED", "SUCCEEDED"])

    def test_started_workflow_invocation_is_not_replayed(self) -> None:
        result = self._issue(mode="DETERMINISTIC")
        run_id = str(result["run_id"])
        capability = str(result["capability"])
        correlation_id = str(uuid4())
        tool_input = {"query": "SYNORA-P1", "limit": 1, "offset": 0}
        digest = args_digest(tool_input)
        key = invocation_id(run_id, 1, "uncertain", "item.lookup", "1", digest)
        frappe.get_doc(
            {
                "doctype": "Synora Workflow Tool Invocation",
                "name": key,
                "invocation_id": key,
                "run": run_id,
                "initiator": BUYER,
                "plan_version": 1,
                "step_id": "uncertain",
                "tool_name": "item.lookup",
                "tool_version": "1",
                "args_digest": digest,
                "status": "STARTED",
                "started_at": now_datetime(),
                "correlation_id": correlation_id,
            }
        ).insert(ignore_permissions=True)
        payload = {
            "schema_version": "1",
            "run_id": run_id,
            "capability": capability,
            "correlation_id": correlation_id,
            "tool": {"name": "item.lookup", "version": "1", "input": tool_input},
            "invocation_id": key,
            "plan_version": 1,
            "step_id": "uncertain",
            "args_digest": digest,
        }
        frappe.set_user("Guest")
        response = execute(**payload)
        self.assertEqual(response["error"]["code"], "CONFLICT")
        self.assertIn("uncertain", response["error"]["message"])

    def test_workflow_invocation_digest_conflict_is_rejected(self) -> None:
        result = self._issue(mode="DETERMINISTIC")
        run_id = str(result["run_id"])
        capability = str(result["capability"])
        correlation_id = str(uuid4())
        original = {"query": "SYNORA-P1", "limit": 1, "offset": 0}
        digest = args_digest(original)
        key = invocation_id(run_id, 1, "read-item", "item.lookup", "1", digest)
        payload = {
            "schema_version": "1",
            "run_id": run_id,
            "capability": capability,
            "correlation_id": correlation_id,
            "tool": {
                "name": "item.lookup",
                "version": "1",
                "input": {"query": "different", "limit": 1, "offset": 0},
            },
            "invocation_id": key,
            "plan_version": 1,
            "step_id": "read-item",
            "args_digest": digest,
        }
        frappe.set_user("Guest")
        response = execute(**payload)
        self.assertEqual(response["error"]["code"], "CONFLICT")

    def test_gateway_rechecks_cancel_after_capability_resolution(self) -> None:
        """A cancel committed after resolution cannot start a read Handler."""
        result = self._issue(mode="DETERMINISTIC")
        run_id = str(result["run_id"])
        capability = str(result["capability"])
        payload = {
            "schema_version": "1",
            "run_id": run_id,
            "capability": capability,
            "correlation_id": str(uuid4()),
            "tool": {
                "name": "item.lookup",
                "version": "1",
                "input": {"query": "SYNORA-P1", "limit": 1, "offset": 0},
            },
        }
        resolved = resolve_run(run_id, capability)
        cancel = cancel_run(run_id, str(uuid4()))
        self.assertTrue(cancel["ok"])
        with patch("synora_agentic_erp.api.resolve_run", return_value=resolved):
            frappe.set_user("Guest")
            response = execute(**payload)
        self.assertEqual(response["error"]["code"], "CONFLICT")
