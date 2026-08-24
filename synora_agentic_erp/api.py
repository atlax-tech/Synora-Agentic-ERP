from typing import Any
from uuid import uuid4

import frappe
from frappe.recorder import do_not_record

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


def _set_status(status_code: int) -> None:
    frappe.local.response.http_status_code = status_code


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
        if run is not None and request is not None:
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
        erp_fault = GatewayFault("ERP_ERROR", "ERP request failed", 502)
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
