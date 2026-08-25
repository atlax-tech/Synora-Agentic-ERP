"""P3.5 plan_run: 可解释只读计划生成、状态流转与权限测试。"""

import frappe
from frappe.tests.utils import FrappeTestCase

from synora_agentic_erp.api import analyze_run, get_run, issue_run, plan_run

BUYER = "synora-p1-buyer@dev.localhost"
ACCOUNTANT = "synora-p1-accountant@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
GOAL = "ensure stock for SYNORA-P1-Item-1001 for the next quarter"
CORRELATION_ID = "3f4a5b6c-7d8e-4f90-a1b2-c3d4e5f6a7b8"


class TestPlanRun(FrappeTestCase):
    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        super().tearDown()

    def _analyzed_run(self) -> dict[str, object]:
        frappe.set_user(BUYER)
        run = issue_run(COMPANY, GOAL, warehouse=WAREHOUSE, correlation_id=CORRELATION_ID)["run"]
        response = analyze_run(str(run["run_id"]), CORRELATION_ID)
        self.assertTrue(response["ok"])
        return run

    def test_plan_run_transitions_proposed_to_succeeded(self) -> None:
        run = self._analyzed_run()
        frappe.set_user(BUYER)
        response = plan_run(str(run["run_id"]), CORRELATION_ID)
        self.assertTrue(response["ok"])
        result = response["plan"]
        self.assertEqual(result["run_state"], "SUCCEEDED")
        stored = frappe.get_doc("Synora Agent Run", run["run_id"])
        self.assertEqual(stored.run_state, "SUCCEEDED")
        # 计划持久化且可解释
        plan_docs = frappe.get_all("Synora Run Plan", filters={"run": run["run_id"]})
        self.assertEqual(len(plan_docs), 1)
        plan_doc = frappe.get_doc("Synora Run Plan", plan_docs[0].name)
        parsed = frappe.parse_json(plan_doc.plan_json)
        self.assertIn("findings", parsed)
        self.assertIn("summary", parsed)
        for finding in parsed["findings"]:
            self.assertTrue(finding["evidence"])
        # 计划是只读的 (无写入动作)
        for finding in parsed["findings"]:
            self.assertNotIn("execute", str(finding).lower())

    def test_plan_run_requires_initiator(self) -> None:
        run = self._analyzed_run()
        frappe.set_user(ACCOUNTANT)
        response = plan_run(str(run["run_id"]), CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "PERMISSION_DENIED")
        stored = frappe.get_doc("Synora Agent Run", run["run_id"])
        self.assertEqual(stored.run_state, "PROPOSED")

    def test_plan_run_rejects_non_proposed_state(self) -> None:
        frappe.set_user(BUYER)
        run = issue_run(COMPANY, GOAL, warehouse=WAREHOUSE, correlation_id=CORRELATION_ID)["run"]
        response = plan_run(str(run["run_id"]), CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "CONFLICT")

    def test_plan_run_after_success_is_conflict(self) -> None:
        run = self._analyzed_run()
        frappe.set_user(BUYER)
        plan_run(str(run["run_id"]), CORRELATION_ID)
        response = plan_run(str(run["run_id"]), CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "CONFLICT")

    def test_get_run_returns_plan(self) -> None:
        run = self._analyzed_run()
        frappe.set_user(BUYER)
        plan_run(str(run["run_id"]), CORRELATION_ID)
        response = get_run(str(run["run_id"]))
        self.assertTrue(response["ok"])
        self.assertEqual(response["run"]["run_state"], "SUCCEEDED")
        self.assertIsNotNone(response["plan"])
        self.assertIn("summary", response["plan"])

    def test_plan_persists_enhancement_evidence(self) -> None:
        """验收修复(阻断2): 增强证据持久化。

        app-test 无 Runtime sidecar -> 回退确定性摘要, 但 provider/token/
        耗时/回退原因证据必须落库并可读, 页面不再是无证据的确定性计划。
        """
        run = self._analyzed_run()
        frappe.set_user(BUYER)
        response = plan_run(str(run["run_id"]), CORRELATION_ID)
        self.assertTrue(response["ok"])
        result = response["plan"]
        plan_result = result["plan"]
        # 回退路径: enhanced_text 等于确定性摘要, evidence 记录 fallback。
        self.assertEqual(plan_result["enhanced_text"], plan_result["summary"])
        self.assertIn("evidence", plan_result)
        self.assertIn("provider", plan_result["evidence"])
        self.assertTrue(plan_result["evidence"]["fallback_reason"])
        # 持久化字段
        plan_docs = frappe.get_all("Synora Run Plan", filters={"run": run["run_id"]})
        self.assertEqual(len(plan_docs), 1)
        plan_doc = frappe.get_doc("Synora Run Plan", plan_docs[0].name)
        self.assertTrue(plan_doc.enhanced_text)
        self.assertTrue(plan_doc.fallback_reason)
        self.assertEqual(plan_doc.completion_tokens, 0)
        # get_run 返回证据
        detail = get_run(str(run["run_id"]))
        self.assertIn("evidence", detail["plan"])
        self.assertTrue(detail["plan"]["evidence"]["fallback_reason"])
