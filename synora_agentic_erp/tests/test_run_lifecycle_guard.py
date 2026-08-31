"""验收修复(阻断3): 过期/撤销 Run 统一入口校验 + 列表 EXPIRED 归一化测试。

修复前 analyze_run/plan_run 只检查 run_state, capability 撤销或 TTL 过期后
仍可进入 ANALYZING/PROPOSED; 修复后所有业务入口经 _load_active_run 统一校验
(ACTIVE/未撤销/未过期/业务状态), list_runs 展示层把 TTL 过期归一化为 EXPIRED。

阻断4/5: 分析失败回退 CREATED 可重试、取消竞态不复活、并发状态推进 CAS
(乐观锁) 与计划唯一幂等。
"""

from datetime import timedelta
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from synora_agentic_erp.agent import service as agent_service
from synora_agentic_erp.agent.service import (
    _set_run_state,
)
from synora_agentic_erp.api import (
    analyze_run,
    cancel_run,
    issue_run,
    list_runs,
    plan_run,
    revoke_run,
)
from synora_agentic_erp.gateway.contract import GatewayFault

BUYER = "synora-p1-buyer@dev.localhost"
ACCOUNTANT = "synora-p1-accountant@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
GOAL = "ensure stock for SYNORA-P1-Item-1001 for the next quarter"
CORRELATION_ID = "7a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"


class TestRunLifecycleGuard(FrappeTestCase):
    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        super().tearDown()

    def _issue(self) -> str:
        frappe.set_user(BUYER)
        response = issue_run(COMPANY, GOAL, warehouse=WAREHOUSE, correlation_id=CORRELATION_ID)
        self.assertTrue(response["ok"])
        return str(response["run"]["run_id"])

    def _expire(self, run_id: str) -> None:
        # expires_at 属 IMMUTABLE_FIELDS, 走 Document.save 会被 validate 拒绝;
        # 模拟"capability 已过期"用 set_value 直接改库 (不触发业务校验)。
        frappe.db.set_value(
            "Synora Agent Run", run_id, "expires_at", now_datetime() - timedelta(seconds=1)
        )

    def test_analyze_rejects_revoked_run(self) -> None:
        run_id = self._issue()
        frappe.set_user(BUYER)
        revoke_response = revoke_run(run_id, CORRELATION_ID)
        self.assertEqual(revoke_response["run"]["status"], "REVOKED")
        response = analyze_run(run_id, CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "CONFLICT")
        stored = frappe.get_doc("Synora Agent Run", run_id)
        self.assertEqual(stored.run_state, "CREATED")  # 未进入 ANALYZING

    def test_analyze_rejects_expired_run(self) -> None:
        run_id = self._issue()
        frappe.set_user(BUYER)
        self._expire(run_id)
        response = analyze_run(run_id, CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "CONFLICT")
        stored = frappe.get_doc("Synora Agent Run", run_id)
        self.assertEqual(stored.run_state, "CREATED")

    def test_plan_rejects_revoked_run(self) -> None:
        run_id = self._issue()
        frappe.set_user(BUYER)
        analyze_run(run_id, CORRELATION_ID)
        revoke_run(run_id, CORRELATION_ID)
        response = plan_run(run_id, CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "CONFLICT")
        stored = frappe.get_doc("Synora Agent Run", run_id)
        self.assertEqual(stored.run_state, "PROPOSED")  # 未进入 SUCCEEDED

    def test_list_runs_normalizes_expired(self) -> None:
        run_id = self._issue()
        self._expire(run_id)
        frappe.set_user(BUYER)
        response = list_runs()
        self.assertTrue(response["ok"])
        self.assertIn("expired", response["runs"][0])
        for entry in response["runs"]:
            if entry["run_id"] == run_id:
                self.assertEqual(entry["run_state"], "EXPIRED")
                self.assertTrue(entry["expired"])
                break
        else:
            self.fail("run not found in list")

    def test_other_users_cannot_see_run(self) -> None:
        run_id = self._issue()
        self._expire(run_id)
        frappe.set_user(ACCOUNTANT)
        response = list_runs()
        self.assertTrue(response["ok"])
        self.assertNotIn(run_id, {entry["run_id"] for entry in response["runs"]})

    def test_analyze_failure_recovers_to_created(self) -> None:
        """工具失败: 回退 CREATED 可重试, 不留 ANALYZING 中间态与部分记录。"""
        run_id = self._issue()
        frappe.set_user(BUYER)

        def boom(request, ctx):
            raise GatewayFault("TOOL_FAILED", "upstream unavailable", 502)

        with mock.patch.object(agent_service, "dispatch", side_effect=boom):
            response = analyze_run(run_id, CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "TOOL_FAILED")
        stored = frappe.get_doc("Synora Agent Run", run_id)
        self.assertEqual(stored.run_state, "CREATED")
        self.assertEqual(frappe.get_all("Synora Item Analysis", filters={"run": run_id}), [])

    def test_analyze_failure_does_not_override_cancel(self) -> None:
        """取消竞态: 分析失败回退不覆盖并发取消结果 (不复活)。"""
        run_id = self._issue()
        frappe.set_user(BUYER)

        def boom_after_cancel(request, ctx):
            cancel_run(run_id, CORRELATION_ID)  # 模拟用户并发取消
            raise GatewayFault("TOOL_FAILED", "upstream unavailable", 502)

        with mock.patch.object(agent_service, "dispatch", side_effect=boom_after_cancel):
            response = analyze_run(run_id, CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "TOOL_FAILED")
        stored = frappe.get_doc("Synora Agent Run", run_id)
        self.assertEqual(stored.run_state, "CANCELLED")  # 保持取消, 不回退 CREATED

    def test_analyze_cas_loser_does_not_recover_other_request(self) -> None:
        """CAS 失败者不能把胜者的 ANALYZING 状态回滚为 CREATED。"""
        run_id = self._issue()
        frappe.set_user(BUYER)
        with mock.patch.object(
            agent_service,
            "_set_run_state",
            side_effect=GatewayFault("CONFLICT", "run state changed concurrently", 409),
        ) as transition:
            with mock.patch.object(agent_service, "_recover_failed_analysis") as recover:
                response = analyze_run(run_id, CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "CONFLICT")
        transition.assert_called_once()
        recover.assert_not_called()
        stored = frappe.get_doc("Synora Agent Run", run_id)
        self.assertEqual(stored.run_state, "CREATED")

    def test_state_transition_cas_rejects_concurrent_change(self) -> None:
        """乐观锁 CAS: 自加载后数据库被并发修改, 状态推进必须失败。"""
        run_id = self._issue()
        frappe.set_user(BUYER)
        loaded = frappe.get_doc("Synora Agent Run", run_id)  # 模拟请求 A 已加载
        other = frappe.get_doc("Synora Agent Run", run_id)  # 模拟请求 B 并发推进
        other.flags.synora_state_change = True
        other.run_state = "ANALYZING"
        other.state_version += 1
        other.save(ignore_permissions=True)
        with self.assertRaises(GatewayFault) as ctx:
            _set_run_state(loaded, "ANALYZING")
        self.assertEqual(ctx.exception.code, "CONFLICT")

    def test_plan_insert_is_idempotent_per_run(self) -> None:
        """计划唯一: run 已存在计划时再次 plan_run 被 DB 唯一约束拦截。"""
        run_id = self._issue()
        frappe.set_user(BUYER)
        analyze_run(run_id, CORRELATION_ID)
        frappe.get_doc(
            {
                "doctype": "Synora Run Plan",
                "run": run_id,
                "goal": GOAL,
                "summary": "already generated",
                "plan_json": '{"summary": "x"}',
                "correlation_id": CORRELATION_ID,
            }
        ).insert(ignore_permissions=True)
        response = plan_run(run_id, CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "CONFLICT")
        self.assertEqual(len(frappe.get_all("Synora Run Plan", filters={"run": run_id})), 1)

    def test_plan_succeeded_failure_compensates_insert(self) -> None:
        """SUCCEEDED 推进失败: 补偿删除本次计划, run 保持 PROPOSED 可重试 (无死锁)。"""
        run_id = self._issue()
        frappe.set_user(BUYER)
        analyze_run(run_id, CORRELATION_ID)
        original = agent_service._set_run_state

        def flaky(run, target):
            if target == "SUCCEEDED":
                raise GatewayFault("CONFLICT", "simulated concurrent failure", 409)
            return original(run, target)

        with mock.patch.object(agent_service, "_set_run_state", side_effect=flaky):
            response = plan_run(run_id, CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "CONFLICT")
        # 补偿删除: 无残留计划, run 仍 PROPOSED, 可重试成功。
        self.assertEqual(frappe.get_all("Synora Run Plan", filters={"run": run_id}), [])
        stored = frappe.get_doc("Synora Agent Run", run_id)
        self.assertEqual(stored.run_state, "PROPOSED")
        retry = plan_run(run_id, CORRELATION_ID)
        self.assertTrue(retry["ok"])
        self.assertEqual(len(frappe.get_all("Synora Run Plan", filters={"run": run_id})), 1)

    def test_runtime_url_rejects_userinfo_bypass(self) -> None:
        """loopback 校验防 SSRF: userinfo@evil host 必须拒绝 (不能只匹配前缀)。"""
        evil = "http://127.0.0.1:8001@evil.example.com:80"
        with mock.patch.dict("os.environ", {"SYNORA_RUNTIME_URL": evil}, clear=False):
            with self.assertRaises(GatewayFault):
                agent_service._runtime_enhance_url()

    def test_runtime_url_accepts_loopback(self) -> None:
        with mock.patch.dict(
            "os.environ", {"SYNORA_RUNTIME_URL": "http://127.0.0.1:8001/"}, clear=False
        ):
            self.assertEqual(agent_service._runtime_enhance_url(), "http://127.0.0.1:8001/enhance")
            self.assertEqual(
                agent_service._runtime_url("coach/answer"),
                "http://127.0.0.1:8001/coach/answer",
            )

    def test_runtime_url_rejects_non_origin_components(self) -> None:
        for configured in (
            "http://127.0.0.1:8001/base",
            "http://127.0.0.1:8001?target=evil",
            "http://127.0.0.1:8001/#fragment",
            "http://127.0.0.1:8001/base?target=evil",
            "http://evil.example.com:8001",
        ):
            with self.subTest(configured=configured):
                with mock.patch.dict("os.environ", {"SYNORA_RUNTIME_URL": configured}, clear=False):
                    with self.assertRaises(GatewayFault):
                        agent_service._runtime_url("coach/answer")

    def test_runtime_url_rejects_host_gateway_without_explicit_config(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "SYNORA_RUNTIME_URL": "http://host.docker.internal:8001",
                "SYNORA_RUNTIME_ALLOW_HOST_GATEWAY": "0",
            },
            clear=False,
        ):
            with self.assertRaises(GatewayFault):
                agent_service._runtime_enhance_url()

    def test_runtime_url_accepts_tokenized_host_gateway(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "SYNORA_RUNTIME_URL": "http://host.docker.internal:8001",
                "SYNORA_RUNTIME_ALLOW_HOST_GATEWAY": "1",
                "SYNORA_RUNTIME_TOKEN": "test-runtime-token",
            },
            clear=False,
        ):
            self.assertEqual(
                agent_service._runtime_enhance_url(),
                "http://host.docker.internal:8001/enhance",
            )

    def test_runtime_non_object_response_falls_back(self) -> None:
        """Runtime 返回非对象 JSON: 回退确定性摘要, 不抛 500。"""

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def read(self, _n):
                return b'["not", "an", "object"]'

        with mock.patch.dict(
            "os.environ", {"SYNORA_RUNTIME_URL": "http://127.0.0.1:8001"}, clear=False
        ):
            fake_opener = mock.Mock()
            fake_opener.open.return_value = FakeResponse()
            with mock.patch.object(
                agent_service.urllib.request, "build_opener", return_value=fake_opener
            ):
                text, evidence = agent_service._enhance_plan_via_runtime({"summary": "确定性摘要"})
        self.assertEqual(text, "确定性摘要")
        self.assertTrue(evidence["fallback_reason"])
