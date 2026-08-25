from typing import Any
from uuid import uuid4

import frappe
from frappe.recorder import do_not_record
from frappe.utils import cint

from synora_agentic_erp.gateway.contract import (
    SCHEMA_VERSION,
    GatewayFault,
    bounded_text,
    canonical_uuid,
    error_response,
    optional_text,
    parse_request,
)
from synora_agentic_erp.gateway.contract import (
    correlation_id as validate_correlation_id,
)
from synora_agentic_erp.gateway.registry import dispatch
from synora_agentic_erp.gateway.security import (
    issue_run as create_run,
)
from synora_agentic_erp.gateway.security import (
    record_gateway_audit,
    reject_mixed_user_credentials,
    require_capability_only_request,
    resolve_run,
)
from synora_agentic_erp.gateway.security import (
    revoke_run as revoke_server_run,
)

# 未认证入口 (execute, allow_guest) 的安全事件日志预算: 每分钟最多记录条数,
# 用于防日志放大; 超出预算的事件静默丢弃。
SECURITY_EVENT_BUDGET = 60


def _set_status(status_code: int) -> None:
    frappe.local.response.http_status_code = status_code


def _log_security_event(code: str, correlation_id: str | None) -> None:
    """记录无法绑定 Run 的 Gateway 失败 (安全事件日志策略)。

    未解析出 Run 的失败请求 (无效/过期/猜测 capability 等) 无法形成 Gateway
    Audit (Audit 绑定 Run), 统一记录为脱敏安全事件: 仅含错误码、correlation
    与来源 IP, 不包含 capability 或请求体内容, 用于探测/滥用模式分析。

    该路径是 allow_guest 未认证入口, 必须按时间窗口节流, 防止伪造请求刷满
    Error Log (日志放大); 超预算的后续事件静默丢弃。
    """
    if not _security_event_budget_allowed():
        return
    ip = getattr(frappe.local, "request_ip", None) or "-"
    frappe.log_error(
        message=(
            f"synora gateway security event (code={code}, correlation={correlation_id}, ip={ip})"
        ),
        title="Synora Gateway Security Event",
    )


def _security_event_budget_allowed() -> bool:
    """每分钟最多记录 SECURITY_EVENT_BUDGET 条安全事件 (Redis 窗口计数)。"""
    import time

    window = int(time.time()) // 60
    key = f"synora:sec-event:{window}"
    if cint(frappe.cache.get(key)) >= SECURITY_EVENT_BUDGET:
        return False
    frappe.cache.incrby(key, 1)
    frappe.cache.expire(key, 120)
    return True


@frappe.whitelist(methods=["POST"])  # type: ignore[untyped-decorator]
@do_not_record  # type: ignore[untyped-decorator]
def issue_run(
    company: str, warehouse: str | None = None, correlation_id: str | None = None
) -> dict[str, Any]:
    safe_correlation_id: str | None = None
    try:
        reject_mixed_user_credentials()
        if correlation_id is None:
            correlation_id = str(uuid4())
        safe_correlation_id = validate_correlation_id(correlation_id)
        safe_company = bounded_text(company, "company")
        safe_warehouse = optional_text(warehouse, "warehouse")
        run = create_run(safe_company, safe_warehouse, safe_correlation_id)
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "correlation_id": safe_correlation_id,
            "run": run,
        }
    except GatewayFault as fault:
        _set_status(fault.status_code)
        return error_response(fault, safe_correlation_id)


@frappe.whitelist(methods=["POST"])  # type: ignore[untyped-decorator]
@do_not_record  # type: ignore[untyped-decorator]
def revoke_run(run_id: str, correlation_id: str) -> dict[str, Any]:
    safe_correlation_id: str | None = None
    try:
        reject_mixed_user_credentials()
        safe_correlation_id = validate_correlation_id(correlation_id)
        safe_run_id = canonical_uuid(run_id, "run_id")
        run = revoke_server_run(safe_run_id, safe_correlation_id)
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "correlation_id": safe_correlation_id,
            "run": run,
        }
    except GatewayFault as fault:
        _set_status(fault.status_code)
        return error_response(fault, safe_correlation_id)


@frappe.whitelist(allow_guest=True, methods=["POST"])  # type: ignore[untyped-decorator]
@do_not_record  # type: ignore[untyped-decorator]
def execute(**payload: Any) -> dict[str, Any]:
    safe_correlation_id: str | None = None
    request = None
    run = None
    try:
        require_capability_only_request()
        # Frappe RPC 路由会把请求路径注入 form_dict.cmd (frappe/api/v1.py),
        # 该键不属于 Gateway 契约, 必须在信任边界剥离后再做严格解析。
        payload.pop("cmd", None)
        request = parse_request(payload)
        safe_correlation_id = request.correlation_id
        run = resolve_run(request.run_id, request.capability)
        result = dispatch(request, run)
        record_gateway_audit(
            run,
            request.tool.name,
            request.tool.version,
            request.correlation_id,
            "SUCCEEDED",
        )
        return result
    except GatewayFault as fault:
        if run is None:
            # 未解析出 Run 的失败请求 (无效/过期/猜测 capability、未知工具等)
            # 无法形成绑定 Run 的 Gateway Audit, 按安全事件日志策略记录脱敏事件,
            # 不包含 capability 或请求体内容。
            _log_security_event(fault.code, safe_correlation_id)
        elif request is not None:
            try:
                record_gateway_audit(
                    run,
                    request.tool.name,
                    request.tool.version,
                    request.correlation_id,
                    "REJECTED",
                    fault.code,
                )
            except Exception:
                fault = GatewayFault("ERP_ERROR", "gateway audit failed", 502)
        _set_status(fault.status_code)
        return error_response(fault, safe_correlation_id)
    except Exception:
        # 响应侧统一脱敏为 ERP_ERROR; 运维日志保留真实异常与调用上下文
        # (frappe.log_error 记录 traceback), 避免诊断只剩统一错误码。
        erp_fault = GatewayFault("ERP_ERROR", "ERP request failed", 502)
        frappe.log_error(
            message=(
                "synora gateway internal error "
                f"(run={run.run_id if run is not None else '-'}, "
                f"correlation={safe_correlation_id})"
            ),
            title="Synora Gateway Internal Error",
        )
        if run is not None and request is not None:
            try:
                record_gateway_audit(
                    run,
                    request.tool.name,
                    request.tool.version,
                    request.correlation_id,
                    "REJECTED",
                    erp_fault.code,
                )
            except Exception:
                erp_fault = GatewayFault("ERP_ERROR", "gateway audit failed", 502)
        _set_status(erp_fault.status_code)
        return error_response(erp_fault, safe_correlation_id)
