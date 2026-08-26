"""Analyze Run: deterministic closeout plus the Phase 4 Agent trace boundary."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from synora_agentic_erp.agent.service import _runtime_failure_response
from synora_agentic_erp.api import (
    analyze_run,
    cancel_run,
    get_run,
    get_run_trace,
    issue_run,
)
from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.gateway.security import resolve_run

BUYER = "synora-p1-buyer@dev.localhost"
ACCOUNTANT = "synora-p1-accountant@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
GOAL = "ensure stock for SYNORA-P1-Item-1001 for the next quarter"
CORRELATION_ID = "9c3f1a2b-4d5e-4f60-a7b8-9c0d1e2f3a4b"


class TestAnalyzeRun(FrappeTestCase):
    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        super().tearDown()

    def _issue(self, *, execution_mode: str | None = None) -> dict[str, object]:
        frappe.set_user(BUYER)
        response = issue_run(
            COMPANY,
            GOAL,
            warehouse=WAREHOUSE,
            correlation_id=CORRELATION_ID,
            execution_mode=execution_mode,
        )
        self.assertTrue(response["ok"])
        return response["run"]

    def test_analyze_run_transitions_created_to_proposed(self) -> None:
        run = self._issue()
        frappe.set_user(BUYER)
        response = analyze_run(str(run["run_id"]), CORRELATION_ID)
        self.assertTrue(response["ok"])
        result = response["analysis"]
        self.assertEqual(result["run_state"], "PROPOSED")
        stored = frappe.get_doc("Synora Agent Run", run["run_id"])
        self.assertEqual(stored.run_state, "PROPOSED")
        self.assertGreaterEqual(stored.state_version, 3)
        # 分析结果按 item 持久化 (存在需求时才产生记录, 但不为空列表断言)。
        analyses = frappe.get_all("Synora Item Analysis", filters={"run": run["run_id"]})
        self.assertEqual(len(analyses), result["items_analyzed"])
        for analysis in analyses:
            doc = frappe.get_doc("Synora Item Analysis", analysis.name)
            self.assertIn(
                doc.risk,
                {"SHORTAGE", "ADEQUATE", "DUPLICATE_RISK", "NO_DEMAND", "NEEDS_INPUT", "UNKNOWN"},
            )

    def test_analyze_run_requires_initiator(self) -> None:
        run = self._issue()
        frappe.set_user(ACCOUNTANT)
        response = analyze_run(str(run["run_id"]), CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "PERMISSION_DENIED")
        stored = frappe.get_doc("Synora Agent Run", run["run_id"])
        self.assertEqual(stored.run_state, "CREATED")

    def test_analyze_run_rejects_non_created_state(self) -> None:
        run = self._issue()
        frappe.set_user(BUYER)
        cancel_response = cancel_run(str(run["run_id"]), CORRELATION_ID)
        self.assertEqual(cancel_response["run"]["run_state"], "CANCELLED")
        response = analyze_run(str(run["run_id"]), CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "CONFLICT")

    def test_get_run_returns_analyses(self) -> None:
        run = self._issue()
        frappe.set_user(BUYER)
        analyze_run(str(run["run_id"]), CORRELATION_ID)
        response = get_run(str(run["run_id"]))
        self.assertTrue(response["ok"])
        self.assertEqual(response["run"]["run_state"], "PROPOSED")
        self.assertIsInstance(response["analyses"], list)

    def test_analysis_records_are_immutable(self) -> None:
        run = self._issue()
        frappe.set_user(BUYER)
        analyze_run(str(run["run_id"]), CORRELATION_ID)
        analyses = frappe.get_all("Synora Item Analysis", filters={"run": run["run_id"]})
        if analyses:
            doc = frappe.get_doc("Synora Item Analysis", analyses[0].name)
            with self.assertRaises(frappe.ValidationError):
                doc.risk = "ADEQUATE"
                doc.save(ignore_permissions=True)

    def test_agent_analyze_persists_trace_then_deterministically_closes(self) -> None:
        run = self._issue(execution_mode="AGENT")
        original_capability = str(run["capability"])
        original_digest = frappe.get_doc("Synora Agent Run", run["run_id"]).capability_digest
        runtime_response = _runtime_failure_response(str(run["run_id"]))
        with patch(
            "synora_agentic_erp.agent.service._execute_agent_via_runtime",
            return_value=runtime_response,
        ):
            frappe.set_user(BUYER)
            response = analyze_run(str(run["run_id"]), CORRELATION_ID)

        self.assertTrue(response["ok"])
        self.assertEqual(response["analysis"]["run_state"], "PROPOSED")
        rotated = frappe.get_doc("Synora Agent Run", run["run_id"])
        self.assertNotEqual(rotated.capability_digest, original_digest)
        with self.assertRaises(GatewayFault):
            resolve_run(str(run["run_id"]), original_capability)
        traces = frappe.get_all(
            "Synora Agent Trace Attempt",
            filters={"run": run["run_id"]},
            fields=["status", "events_count", "stop_reason"],
        )
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].status, "FALLBACK")
        self.assertEqual(traces[0].events_count, 2)
        self.assertIn("MODEL_ERROR", traces[0].stop_reason)

        detail = get_run(str(run["run_id"]))
        self.assertTrue(detail["ok"])
        self.assertEqual(detail["run"]["execution_mode"], "AGENT")
        self.assertEqual(detail["run"]["agent_status"], "FALLBACK")
        self.assertEqual(detail["run"]["agent_trace"]["events_count"], 2)

        trace = get_run_trace(str(run["run_id"]), limit=1)
        self.assertTrue(trace["ok"])
        self.assertEqual(trace["trace"]["total"], 2)
        self.assertEqual(len(trace["trace"]["events"]), 1)
        self.assertEqual(trace["trace"]["events"][0]["event_type"], "run.started")

    def test_agent_boundary_commits_capability_and_trace_before_closeout(self) -> None:
        run = self._issue(execution_mode="AGENT")
        runtime_response = _runtime_failure_response(str(run["run_id"]))
        original_commit = frappe.db.commit
        commit_calls = 0

        def counted_commit() -> None:
            nonlocal commit_calls
            commit_calls += 1
            original_commit()

        with (
            patch(
                "synora_agentic_erp.agent.service._execute_agent_via_runtime",
                return_value=runtime_response,
            ),
            patch.object(frappe.db, "commit", side_effect=counted_commit),
        ):
            frappe.set_user(BUYER)
            response = analyze_run(str(run["run_id"]), CORRELATION_ID)

        self.assertTrue(response["ok"])
        self.assertGreaterEqual(commit_calls, 2)
        self.assertEqual(
            len(frappe.get_all("Synora Agent Trace Attempt", filters={"run": run["run_id"]})),
            1,
        )

    def test_agent_trace_is_not_visible_to_another_user(self) -> None:
        run = self._issue(execution_mode="AGENT")
        frappe.set_user(ACCOUNTANT)
        response = get_run_trace(str(run["run_id"]))
        self.assertEqual(response["error"]["code"], "RUN_REJECTED")
