"""P3.3 analyze_run: 确定性分析触发、状态机流转、权限与结果持久化测试。"""

import frappe
from frappe.tests.utils import FrappeTestCase

from synora_agentic_erp.api import analyze_run, cancel_run, get_run, issue_run

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

    def _issue(self) -> dict[str, object]:
        frappe.set_user(BUYER)
        response = issue_run(COMPANY, GOAL, warehouse=WAREHOUSE, correlation_id=CORRELATION_ID)
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
