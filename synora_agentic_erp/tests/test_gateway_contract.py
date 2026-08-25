from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from synora_agentic_erp.api import execute, issue_run, revoke_run
from synora_agentic_erp.gateway.contract import (
    GatewayFault,
    InputField,
    ToolResult,
    bounded_text,
    parse_request,
)
from synora_agentic_erp.gateway.registry import register
from synora_agentic_erp.gateway.security import (
    reject_mixed_user_credentials,
    require_capability_only_request,
    resolve_run,
)

BUYER = "synora-p1-buyer@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
GOAL = "ensure stock for SYNORA-P1-Item-1001 for the next quarter"
CORRELATION_ID = "1f7f6772-b3a1-4a09-a03c-4f80f845aef8"


class _RecorderProbe:
    def __init__(self) -> None:
        self.cleaned = False

    def cleanup(self) -> None:
        self.cleaned = True


@register(
    name="contract.probe",
    version="1",
    required_doctypes=("Item",),
    input_fields={"query": InputField(lambda value: bounded_text(value, "query"), required=True)},
)
def _contract_probe(_run: object, tool_input: dict[str, object]) -> ToolResult:
    return ToolResult(items=[{"value": str(tool_input["query"])}])


@register(
    name="contract.timeout-probe",
    version="1",
    required_doctypes=("Item",),
    input_fields={},
    timeout_ms=0,
)
def _timeout_probe(_run: object, _tool_input: dict[str, object]) -> ToolResult:
    return ToolResult(items=[])


@register(
    name="contract.error-probe",
    version="1",
    required_doctypes=("Item",),
    input_fields={},
)
def _error_probe(_run: object, _tool_input: dict[str, object]) -> ToolResult:
    raise RuntimeError("internal detail must not escape")


def _payload(run: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "1",
        "run_id": run["run_id"],
        "capability": run["capability"],
        "correlation_id": CORRELATION_ID,
        "tool": {"name": "not.registered", "version": "1", "input": {}},
    }


class TestGatewayContract(FrappeTestCase):
    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        super().tearDown()

    def _issue(self) -> dict[str, object]:
        frappe.set_user(BUYER)
        response = issue_run(COMPANY, GOAL, correlation_id=CORRELATION_ID)
        result = response["run"]
        self.assertNotIn("error", result)
        return result

    def test_run_identity_and_capability_are_server_bound(self) -> None:
        result = self._issue()
        run = frappe.get_doc("Synora Agent Run", result["run_id"])

        self.assertEqual(run.initiator, BUYER)
        self.assertEqual(run.company_scope, COMPANY)
        self.assertNotEqual(run.capability_digest, result["capability"])
        self.assertEqual(
            resolve_run(str(result["run_id"]), str(result["capability"])).initiator, BUYER
        )

    def test_unknown_expired_and_mismatched_capabilities_fail_closed(self) -> None:
        result = self._issue()
        with self.assertRaises(GatewayFault):
            resolve_run(str(result["run_id"]), "forged")
        with self.assertRaises(GatewayFault):
            resolve_run("unknown", str(result["capability"]))

        frappe.db.set_value(
            "Synora Agent Run",
            result["run_id"],
            "expires_at",
            now_datetime() - timedelta(seconds=1),
            update_modified=False,
        )
        with self.assertRaises(GatewayFault):
            resolve_run(str(result["run_id"]), str(result["capability"]))

    def test_gateway_rejects_spoofed_identity_and_unknown_tool(self) -> None:
        result = self._issue()
        payload = _payload(result)
        payload["initiator"] = "Administrator"
        with self.assertRaises(GatewayFault):
            parse_request(payload)

        frappe.set_user("Guest")
        response = execute(**_payload(result))
        self.assertEqual(response["error"]["code"], "TOOL_NOT_ALLOWED")
        self.assertNotIn(str(result["capability"]), str(response))

    def test_execute_strips_frappe_rpc_cmd_injection(self) -> None:
        # Frappe RPC 路由会把请求路径注入 form_dict.cmd (frappe/api/v1.py);
        # 真实 HTTP 下 execute 必须剥离该键后再做严格契约解析。
        result = self._issue()
        payload = _payload(result)
        payload["cmd"] = "synora_agentic_erp.api.execute"
        frappe.set_user("Guest")
        response = execute(**payload)
        self.assertEqual(response["error"]["code"], "TOOL_NOT_ALLOWED")

    def test_registered_tool_has_strict_input_typed_output_and_audit(self) -> None:
        result = self._issue()
        payload = _payload(result)
        payload["tool"] = {
            "name": "contract.probe",
            "version": "1",
            "input": {"query": "bearing", "limit": 1, "offset": 0},
        }
        frappe.set_user("Guest")
        response = execute(**payload)
        self.assertTrue(response["ok"])
        self.assertEqual(response["tool"]["risk"], "READ")
        self.assertEqual(response["data"], [{"value": "bearing"}])
        self.assertEqual(response["completeness"], {"status": "COMPLETE", "omissions": {}})
        self.assertEqual(response["page"]["limit"], 1)
        self.assertIn("erpnext_revision", response["snapshot"])
        self.assertEqual(frappe.session.user, "Guest")

        frappe.set_user("Administrator")
        audit = frappe.get_last_doc("Synora Gateway Audit")
        self.assertEqual(audit.initiator, BUYER)
        self.assertEqual(audit.correlation_id, CORRELATION_ID)
        self.assertEqual(audit.outcome, "SUCCEEDED")

    def test_registered_tool_rejects_unknown_fields_and_page_over_limit(self) -> None:
        result = self._issue()
        for tool_input in (
            {"query": "bearing", "initiator": "Administrator"},
            {"query": "bearing", "limit": 51},
        ):
            payload = _payload(result)
            payload["tool"] = {
                "name": "contract.probe",
                "version": "1",
                "input": tool_input,
            }
            frappe.set_user("Guest")
            response = execute(**payload)
            self.assertEqual(response["error"]["code"], "INVALID_INPUT")
            frappe.set_user(BUYER)

    def test_registered_tool_classifies_post_hoc_timeout(self) -> None:
        # timeout_ms 是执行完成后的耗时分类阈值 (post-hoc), 不中断执行;
        # TIMEOUT 在错误契约中标记 retryable。
        result = self._issue()
        payload = _payload(result)
        payload["tool"] = {"name": "contract.timeout-probe", "version": "1", "input": {}}
        frappe.set_user("Guest")
        response = execute(**payload)
        self.assertEqual(response["error"]["code"], "TIMEOUT")
        self.assertTrue(response["error"]["retryable"])

    def test_unexpected_tool_failure_is_sanitized_and_audited(self) -> None:
        result = self._issue()
        payload = _payload(result)
        payload["tool"] = {"name": "contract.error-probe", "version": "1", "input": {}}
        frappe.set_user("Guest")
        with patch("synora_agentic_erp.api.frappe.log_error") as mock_log:
            response = execute(**payload)
        self.assertEqual(response["error"]["code"], "ERP_ERROR")
        self.assertNotIn("internal detail", str(response))
        # 诊断日志保留真实异常 (脱敏响应之外), 避免运维只剩统一错误码。
        self.assertTrue(mock_log.called)
        logged = " ".join(str(call) for call in mock_log.call_args_list)
        self.assertIn("internal", logged)
        # 已解析 Run 的失败仍形成绑定 Run 的 Gateway Audit。
        frappe.set_user("Administrator")
        audit = frappe.get_last_doc("Synora Gateway Audit")
        self.assertEqual(audit.outcome, "REJECTED")
        self.assertEqual(audit.error_code, "ERP_ERROR")

    def test_unresolvable_run_failure_is_logged_as_security_event(self) -> None:
        # 无效/猜测 capability 无法解析出 Run -> 不形成 Gateway Audit,
        # 按安全事件日志策略记录脱敏事件 (不含 capability)。
        forged_capability = "f" * 43
        frappe.set_user("Guest")
        with patch("synora_agentic_erp.api.frappe.log_error") as mock_log:
            response = execute(
                schema_version="1",
                run_id=str(uuid4()),
                capability=forged_capability,
                correlation_id=str(uuid4()),
                tool={"name": "item.lookup", "version": "1", "input": {}},
            )
        self.assertEqual(response["error"]["code"], "RUN_REJECTED")
        self.assertTrue(mock_log.called)
        logged = " ".join(str(call) for call in mock_log.call_args_list)
        self.assertIn("security event", logged)
        self.assertNotIn(forged_capability, logged)

    def test_issue_and_revoke_validate_runtime_types(self) -> None:
        frappe.set_user(BUYER)
        response = issue_run(42, GOAL, correlation_id=CORRELATION_ID)  # type: ignore[arg-type]
        self.assertEqual(response["error"]["code"], "INVALID_INPUT")
        response = issue_run(COMPANY, "", correlation_id=CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "INVALID_INPUT")
        response = issue_run(COMPANY, "x" * (1000 + 1), correlation_id=CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "INVALID_INPUT")
        response = revoke_run("not-a-uuid", CORRELATION_ID)
        self.assertEqual(response["error"]["code"], "INVALID_INPUT")

    def test_gateway_rejects_user_and_mixed_credentials(self) -> None:
        frappe.set_user(BUYER)
        with self.assertRaises(GatewayFault):
            require_capability_only_request({})
        frappe.set_user("Guest")
        with self.assertRaises(GatewayFault):
            require_capability_only_request({"Authorization": "Bearer secret"})
        with self.assertRaises(GatewayFault):
            require_capability_only_request({"Cookie": "sid=secret"})
        frappe.set_user(BUYER)
        with self.assertRaises(GatewayFault):
            reject_mixed_user_credentials({"Authorization": "Bearer bad", "Cookie": "sid=valid"})

    def test_run_is_immutable_and_revocation_is_audited(self) -> None:
        result = self._issue()
        run = frappe.get_doc("Synora Agent Run", result["run_id"])
        run.initiator = "Administrator"
        with self.assertRaises(frappe.ValidationError):
            run.save(ignore_permissions=True)

        response = revoke_run(str(result["run_id"]), CORRELATION_ID)
        self.assertEqual(response["run"]["status"], "REVOKED")
        revoked = frappe.get_doc("Synora Agent Run", result["run_id"])
        self.assertEqual(revoked.revoked_by, BUYER)
        self.assertEqual(revoked.state_version, 2)
        with self.assertRaises(GatewayFault):
            resolve_run(str(result["run_id"]), str(result["capability"]))

    def test_invalid_correlation_is_not_reflected(self) -> None:
        frappe.set_user(BUYER)
        secret = "capability-looking-secret"
        response = issue_run(COMPANY, GOAL, correlation_id=secret)
        self.assertIsNone(response["correlation_id"])
        self.assertNotIn(secret, str(response))

    def test_sensitive_endpoints_disable_frappe_recorder_before_handling(self) -> None:
        recorder = _RecorderProbe()
        frappe.local._recorder = recorder
        self._issue()
        self.assertTrue(recorder.cleaned)
        self.assertFalse(hasattr(frappe.local, "_recorder"))
