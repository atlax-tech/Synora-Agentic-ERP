"""Analyze Run: deterministic closeout plus the Phase 4 Agent trace boundary."""

import hashlib
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from synora_agentic_erp.agent import service as agent_service
from synora_agentic_erp.agent.service import (
    _runtime_failure_response,
    _validate_agent_runtime_response,
)
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
NATIVE_PROFILE_HASH = (
    "1a676172e121c37910512c73b4a77cf3955cad7bca2c659f342d5b2c6e9dbda4"
)


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

    def test_runtime_final_answer_survives_redacted_frappe_boundary(self) -> None:
        run_id = "37e1d8a5-1730-4ad0-bffd-217774ed9fab"
        summary = "stock is adequate"
        digest = hashlib.sha256(summary.encode()).hexdigest()
        body = _runtime_failure_response(run_id)
        result = body["result"]
        result["stop_reason"]["code"] = "FINAL_ANSWER"
        result["final_answer"] = {
            "schema_version": "1",
            "status": "SUCCEEDED",
            "summary": summary,
            "evidence_refs": [digest],
            "unknowns": ["lead time not observed"],
        }
        timestamp = result["events"][0]["timestamp"]
        result["events"] = [
            result["events"][0],
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 2,
                "event_type": "action.proposed",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {"step": 1, "tool_name": "stock.projected"},
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 3,
                "event_type": "action.validated",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {"step": 1, "tool_name": "stock.projected"},
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 4,
                "event_type": "tool.started",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {"step": 1, "tool_name": "stock.projected"},
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 5,
                "event_type": "tool.observed",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {
                    "step": 1,
                    "tool_name": "stock.projected",
                    "ok": True,
                    "summary": summary,
                    "digest": digest,
                },
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 6,
                "event_type": "final.proposed",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {
                    key: result["final_answer"][key]
                    for key in ("status", "summary", "evidence_refs", "unknowns")
                },
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 7,
                "event_type": "final.validated",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {"step": 1},
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 8,
                "event_type": "run.stopped",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {"code": "FINAL_ANSWER", "step": 1, "detail": "done"},
            },
        ]

        validated = _validate_agent_runtime_response(body, run_id)

        self.assertEqual(
            validated["result"]["final_answer"],
            result["final_answer"],
        )

    def test_historical_prompt_v1_runtime_trace_remains_readable(self) -> None:
        run_id = "37e1d8a5-1730-4ad0-bffd-217774ed9fab"
        body = _runtime_failure_response(run_id)
        body["prompt_schema_version"] = "1"

        validated = _validate_agent_runtime_response(body, run_id)

        self.assertEqual(validated["prompt_schema_version"], "1")

    def test_prompt_v2_model_request_requires_context_metadata(self) -> None:
        run_id = "37e1d8a5-1730-4ad0-bffd-217774ed9fab"
        body = _runtime_failure_response(run_id)
        timestamp = body["result"]["events"][0]["timestamp"]
        context_payload = {
            "step": 1,
            "context_builder_version": "1",
            "instruction_schema_version": "2",
            "instruction_profile_id": "native-agent",
            "instruction_profile_hash": NATIVE_PROFILE_HASH,
            "skill_refs": [],
            "selected_fragment_ids": ["prompt:native-agent:a", "goal:caller"],
            "dropped_fragment_ids": [],
            "estimated_input_units_before": 900,
            "estimated_input_units_after": 900,
            "input_budget": 1_000,
            "compression_reasons": [],
            "effective_tool_names": ["item.lookup"],
        }
        result = body["result"]
        result["events"] = [
            result["events"][0],
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 2,
                "event_type": "model.requested",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {"step": 1, "tool_count": 1},
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 3,
                "event_type": "run.stopped",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {"code": "MODEL_ERROR", "step": 1, "detail": "failed"},
            },
        ]
        result["stop_reason"]["code"] = "MODEL_ERROR"
        with self.assertRaises(ValueError):
            _validate_agent_runtime_response(body, run_id)

        result["events"] = [
            result["events"][0],
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 2,
                "event_type": "context.assembled",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": context_payload,
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 3,
                "event_type": "model.requested",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {"step": 1, "tool_count": 1},
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 4,
                "event_type": "context.assembled",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {**context_payload, "actual_prompt_tokens": 900},
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 5,
                "event_type": "run.stopped",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {"code": "MODEL_ERROR", "step": 1, "detail": "failed"},
            },
        ]
        validated = _validate_agent_runtime_response(body, run_id)
        self.assertEqual(
            validated["result"]["events"][1]["event_type"],
            "context.assembled",
        )

    def test_prompt_v2_context_metadata_rejects_raw_content_and_tool_expansion(self) -> None:
        run_id = "37e1d8a5-1730-4ad0-bffd-217774ed9fab"
        body = _runtime_failure_response(run_id, code="CONTEXT_BUDGET")
        timestamp = body["result"]["events"][0]["timestamp"]
        payload = {
            "step": 1,
            "context_builder_version": "1",
            "instruction_schema_version": "2",
            "instruction_profile_id": "native-agent",
            "instruction_profile_hash": NATIVE_PROFILE_HASH,
            "skill_refs": [],
            "selected_fragment_ids": ["goal:caller"],
            "dropped_fragment_ids": [],
            "estimated_input_units_before": 1_000,
            "estimated_input_units_after": 900,
            "input_budget": 900,
            "compression_reasons": ["bounded summary applied"],
            "effective_tool_names": ["purchase.submit"],
            "content": "must not cross the trace boundary",
        }
        body["result"]["events"] = [
            body["result"]["events"][0],
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 2,
                "event_type": "context.assembled",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": payload,
            },
            body["result"]["events"][1],
        ]
        with self.assertRaises(ValueError):
            _validate_agent_runtime_response(body, run_id)

    def test_prompt_v2_context_metadata_rejects_forged_profile_hash(self) -> None:
        run_id = "37e1d8a5-1730-4ad0-bffd-217774ed9fab"
        body = _runtime_failure_response(run_id, code="CONTEXT_BUDGET")
        timestamp = body["result"]["events"][0]["timestamp"]
        payload = {
            "step": 1,
            "context_builder_version": "1",
            "instruction_schema_version": "2",
            "instruction_profile_id": "native-agent",
            "instruction_profile_hash": "f" * 64,
            "skill_refs": [],
            "selected_fragment_ids": ["goal:caller"],
            "dropped_fragment_ids": [],
            "estimated_input_units_before": 900,
            "estimated_input_units_after": 900,
            "input_budget": 1_000,
            "compression_reasons": [],
            "effective_tool_names": ["item.lookup"],
        }
        body["result"]["events"] = [
            body["result"]["events"][0],
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 2,
                "event_type": "context.assembled",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": payload,
            },
            body["result"]["events"][1],
        ]
        with self.assertRaises(ValueError):
            _validate_agent_runtime_response(body, run_id)

    def test_prompt_v2_skill_event_requires_known_manifest_and_tool_subset(self) -> None:
        run_id = "37e1d8a5-1730-4ad0-bffd-217774ed9fab"
        body = _runtime_failure_response(run_id)
        timestamp = body["result"]["events"][0]["timestamp"]
        context_payload = {
            "step": 1,
            "context_builder_version": "1",
            "instruction_schema_version": "2",
            "instruction_profile_id": "native-agent",
            "instruction_profile_hash": NATIVE_PROFILE_HASH,
            "skill_refs": ["skill:duplicate-purchase-check:body"],
            "selected_fragment_ids": ["goal:caller", "skill:duplicate-purchase-check:body"],
            "dropped_fragment_ids": [],
            "estimated_input_units_before": 900,
            "estimated_input_units_after": 900,
            "input_budget": 1_000,
            "compression_reasons": [],
            "effective_tool_names": ["material_request.open", "purchase_order.open"],
        }
        skill_payload = {
            "step": 1,
            "skill_id": "duplicate-purchase-check",
            "skill_version": "1.0.0",
            "skill_manifest_hash": (
                "7dafd44000576e93f72a3f9c9e16b5cf0a1764b1aa04087dee45c959b53f7d69"
            ),
            "disclosure_level": 2,
            "effective_tool_names": ["material_request.open", "purchase_order.open"],
            "load_reason": "server task profile REPLENISHMENT_ANALYSIS; 0 triggered reference(s)",
        }
        events = body["result"]["events"]
        body["result"]["events"] = [
            events[0],
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 2,
                "event_type": "skill.loaded",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": skill_payload,
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 3,
                "event_type": "context.assembled",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": context_payload,
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 4,
                "event_type": "model.requested",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {"step": 1, "tool_count": 2},
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 5,
                "event_type": "context.assembled",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {**context_payload, "actual_prompt_tokens": 900},
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 6,
                "event_type": "run.stopped",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {"code": "MODEL_ERROR", "step": 1, "detail": "failed"},
            },
        ]
        _validate_agent_runtime_response(body, run_id)

        skill_payload["skill_manifest_hash"] = "f" * 64
        with self.assertRaises(ValueError):
            _validate_agent_runtime_response(body, run_id)

    def test_runtime_final_answer_rejects_unowned_or_tampered_evidence(self) -> None:
        run_id = "37e1d8a5-1730-4ad0-bffd-217774ed9fab"
        summary = "observed stock is 10"
        digest = hashlib.sha256(summary.encode()).hexdigest()
        body = _runtime_failure_response(run_id)
        result = body["result"]
        result["stop_reason"]["code"] = "FINAL_ANSWER"
        result["final_answer"] = {
            "schema_version": "1",
            "status": "SUCCEEDED",
            "summary": summary,
            "evidence_refs": [digest],
            "unknowns": [],
        }
        timestamp = result["events"][0]["timestamp"]
        result["events"] = [
            result["events"][0],
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 2,
                "event_type": "tool.started",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {},
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 3,
                "event_type": "tool.observed",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {"ok": True, "summary": summary, "digest": digest},
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 4,
                "event_type": "final.proposed",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {
                    key: result["final_answer"][key]
                    for key in ("status", "summary", "evidence_refs", "unknowns")
                },
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 5,
                "event_type": "final.validated",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {},
            },
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": 6,
                "event_type": "run.stopped",
                "timestamp": timestamp,
                "payload_version": "1",
                "payload": {"code": "FINAL_ANSWER"},
            },
        ]

        result["events"][2]["payload"]["digest"] = "a" * 64
        with self.assertRaises(ValueError):
            _validate_agent_runtime_response(body, run_id)
        result["events"][2]["payload"]["digest"] = digest
        result["events"][-1]["payload"]["code"] = "MODEL_ERROR"
        with self.assertRaises(ValueError):
            _validate_agent_runtime_response(body, run_id)
        result["events"][-1]["payload"]["code"] = "FINAL_ANSWER"
        result["final_answer"]["summary"] = "observed stock is 999"
        result["events"][3]["payload"]["summary"] = "observed stock is 999"
        with self.assertRaises(ValueError):
            _validate_agent_runtime_response(body, run_id)

    def test_cancelled_run_is_rechecked_before_next_deterministic_tool(self) -> None:
        """A cancel between two reads must prevent the second ERP call."""
        run = self._issue()
        calls = 0
        original_call = agent_service._call_tool

        def call_then_cancel(ctx, name, tool_input, correlation_id):
            nonlocal calls
            result = original_call(ctx, name, tool_input, correlation_id)
            calls += 1
            if calls == 1:
                cancel_response = cancel_run(str(run["run_id"]), CORRELATION_ID)
                self.assertTrue(cancel_response["ok"])
            return result

        with patch.object(agent_service, "_call_tool", side_effect=call_then_cancel):
            frappe.set_user(BUYER)
            response = analyze_run(str(run["run_id"]), CORRELATION_ID)

        self.assertEqual(response["error"]["code"], "CONFLICT")
        self.assertEqual(calls, 1)
        stored = frappe.get_doc("Synora Agent Run", run["run_id"])
        self.assertEqual(stored.run_state, "CANCELLED")

    def test_agent_trace_is_not_visible_to_another_user(self) -> None:
        run = self._issue(execution_mode="AGENT")
        frappe.set_user(ACCOUNTANT)
        response = get_run_trace(str(run["run_id"]))
        self.assertEqual(response["error"]["code"], "RUN_REJECTED")
