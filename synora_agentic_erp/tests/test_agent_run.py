"""P3.2 Agent Run: 目标输入、确定性状态机、取消与页面数据端点测试。"""

import frappe
from frappe.tests.utils import FrappeTestCase

from synora_agentic_erp.api import (
    available_scope,
    cancel_run,
    get_run,
    issue_run,
    list_runs,
)
from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.gateway.security import resolve_run

BUYER = "synora-p1-buyer@dev.localhost"
ACCOUNTANT = "synora-p1-accountant@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
GOAL = "ensure stock for SYNORA-P1-Item-1001 for the next quarter"
CORRELATION_ID = "6f2a3c4d-8e9f-4a0b-9c1d-2e3f4a5b6c7d"


class TestAgentRun(FrappeTestCase):
    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        super().tearDown()

    def _issue(
        self,
        user: str = BUYER,
        goal: str = GOAL,
        warehouse: str | None = WAREHOUSE,
        time_window_days: int | None = None,
    ) -> dict[str, object]:
        frappe.set_user(user)
        response = issue_run(
            COMPANY,
            goal,
            warehouse=warehouse,
            time_window_days=time_window_days,
            correlation_id=CORRELATION_ID,
        )
        self.assertTrue(response["ok"])
        return response["run"]

    def test_issue_run_persists_goal_and_default_time_window(self) -> None:
        result = self._issue()
        run = frappe.get_doc("Synora Agent Run", result["run_id"])
        self.assertEqual(run.initiator, BUYER)
        self.assertEqual(run.goal, GOAL)
        # P3.1 批准: time_window 缺省 = 当前库存 + 在途 + 未来 90 天需求。
        self.assertEqual(run.time_window_days, 90)
        self.assertEqual(run.run_state, "CREATED")
        self.assertEqual(result["run_state"], "CREATED")
        # 新 Run 可直接签发 capability 供后续只读分析使用。
        resolved = resolve_run(str(result["run_id"]), str(result["capability"]))
        self.assertEqual(resolved.company, COMPANY)

    def test_issue_run_accepts_explicit_time_window(self) -> None:
        result = self._issue(time_window_days=30)
        run = frappe.get_doc("Synora Agent Run", result["run_id"])
        self.assertEqual(run.time_window_days, 30)

    def test_issue_run_rejects_invalid_inputs(self) -> None:
        frappe.set_user(BUYER)
        for days in (0, -1, 366):
            response = issue_run(
                COMPANY,
                GOAL,
                warehouse=WAREHOUSE,
                time_window_days=days,
                correlation_id=CORRELATION_ID,
            )
            self.assertEqual(response["error"]["code"], "INVALID_INPUT")
        # 空 goal 与超长 goal 都 fail closed。
        response = issue_run(COMPANY, "   ", warehouse=WAREHOUSE, correlation_id=CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "INVALID_INPUT")
        response = issue_run(
            COMPANY, "x" * 1001, warehouse=WAREHOUSE, correlation_id=CORRELATION_ID
        )
        self.assertEqual(response["error"]["code"], "INVALID_INPUT")

    def test_goal_and_scope_are_immutable(self) -> None:
        result = self._issue()
        run = frappe.get_doc("Synora Agent Run", result["run_id"])
        run.goal = "changed goal"
        with self.assertRaises(frappe.ValidationError):
            run.save(ignore_permissions=True)
        run = frappe.get_doc("Synora Agent Run", result["run_id"])
        self.assertEqual(run.goal, GOAL)

    def test_cancel_created_run_transitions_and_revokes_capability(self) -> None:
        result = self._issue()
        frappe.set_user(BUYER)
        response = cancel_run(str(result["run_id"]), CORRELATION_ID)
        self.assertEqual(response["run"]["run_state"], "CANCELLED")
        run = frappe.get_doc("Synora Agent Run", result["run_id"])
        self.assertEqual(run.run_state, "CANCELLED")
        self.assertEqual(run.status, "REVOKED")
        self.assertEqual(run.state_version, 2)
        # 取消后 capability 失效, 不能再执行工具。
        with self.assertRaises(GatewayFault):
            resolve_run(str(result["run_id"]), str(result["capability"]))
        # 已取消的 Run 不能再取消。
        frappe.set_user(BUYER)
        response = cancel_run(str(result["run_id"]), CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "CONFLICT")

    def test_cancel_requires_initiator_or_system_manager(self) -> None:
        result = self._issue(user=BUYER)
        frappe.set_user(ACCOUNTANT)
        response = cancel_run(str(result["run_id"]), CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "PERMISSION_DENIED")
        run = frappe.get_doc("Synora Agent Run", result["run_id"])
        self.assertEqual(run.run_state, "CREATED")

    def test_list_runs_shows_only_own_runs(self) -> None:
        self._issue(user=BUYER)
        frappe.set_user(BUYER)
        response = list_runs()
        self.assertTrue(response["ok"])
        self.assertGreaterEqual(response["count"], 1)
        for run in response["runs"]:
            self.assertEqual(run["initiator"], BUYER)
        frappe.set_user(ACCOUNTANT)
        accountant_runs = list_runs()
        self.assertTrue(all(run["initiator"] == ACCOUNTANT for run in accountant_runs["runs"]))

    def test_get_run_isolates_other_users_runs(self) -> None:
        result = self._issue(user=BUYER)
        frappe.set_user(BUYER)
        response = get_run(str(result["run_id"]))
        self.assertTrue(response["ok"])
        self.assertEqual(response["run"]["run_state"], "CREATED")
        self.assertEqual(response["run"]["goal"], GOAL)
        # 他人无权读取 (不泄露存在性, 返回 RUN_REJECTED)。
        frappe.set_user(ACCOUNTANT)
        response = get_run(str(result["run_id"]))
        self.assertEqual(response["error"]["code"], "RUN_REJECTED")

    def test_available_scope_returns_accessible_companies(self) -> None:
        frappe.set_user(BUYER)
        response = available_scope()
        self.assertTrue(response["ok"])
        companies = {entry["company"] for entry in response["scope"]}
        self.assertIn(COMPANY, companies)
