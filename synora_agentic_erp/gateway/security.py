import hashlib
import hmac
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import frappe
from frappe.utils import get_datetime, now_datetime

from synora_agentic_erp.agent.state_machine import (
    CANCELLABLE_STATES,
    validate_transition,
)
from synora_agentic_erp.gateway.contract import GatewayFault

CAPABILITY_AUDIENCE = "synora-agent-runtime"
CAPABILITY_TTL = timedelta(minutes=5)
WORKFLOW_TTL = timedelta(hours=24)
EXECUTION_MODES = frozenset({"DETERMINISTIC", "AGENT", "PLAN_EXECUTE"})


@dataclass(frozen=True)
class RunContext:
    run_id: str
    initiator: str
    company: str
    warehouse: str | None
    state_version: int


def _digest(run_id: str, capability: str) -> str:
    return hashlib.sha256(f"{run_id}:{capability}".encode()).hexdigest()


def _current_headers() -> Mapping[str, str]:
    request = getattr(frappe.local, "request", None)
    return request.headers if request is not None else {}


def require_capability_only_request(headers: Mapping[str, str] | None = None) -> None:
    supplied_headers = headers if headers is not None else _current_headers()
    has_user_credential = any(
        supplied_headers.get(name) for name in ("Authorization", "Cookie", "X-Frappe-CSRF-Token")
    )
    if frappe.session.user != "Guest" or has_user_credential:
        raise GatewayFault("AUTHENTICATION_REJECTED", "gateway accepts only run capability", 401)


def reject_mixed_user_credentials(headers: Mapping[str, str] | None = None) -> None:
    supplied_headers = headers if headers is not None else _current_headers()
    if supplied_headers.get("Authorization") and supplied_headers.get("Cookie"):
        raise GatewayFault("AUTHENTICATION_REJECTED", "mixed user credentials are not allowed", 401)


def issue_run(
    company: str,
    goal: str,
    warehouse: str | None,
    time_window_days: int,
    correlation_id: str,
    execution_mode: str = "DETERMINISTIC",
) -> dict[str, str | int | None]:
    initiator = frappe.session.user
    if not initiator or initiator == "Guest":
        raise GatewayFault("AUTHENTICATION_REQUIRED", "authenticated user required", 401)
    if not isinstance(execution_mode, str) or execution_mode not in EXECUTION_MODES:
        raise GatewayFault("INVALID_INPUT", "execution_mode is invalid")
    if company not in frappe.get_list("Company", pluck="name", filters={"name": company}, limit=1):
        raise GatewayFault("SCOPE_DENIED", "requested scope is not available", 403)
    if warehouse:
        allowed_warehouse = frappe.get_list(
            "Warehouse",
            pluck="name",
            filters={"name": warehouse, "company": company, "disabled": 0},
            limit=1,
        )
        if warehouse not in allowed_warehouse:
            raise GatewayFault("SCOPE_DENIED", "requested scope is not available", 403)

    run_id = str(uuid.uuid4())
    capability = secrets.token_urlsafe(32)
    issued_at = now_datetime()
    expires_at = issued_at + CAPABILITY_TTL
    workflow_expires_at = issued_at + WORKFLOW_TTL if execution_mode == "PLAN_EXECUTE" else None
    # This is a Synora-owned internal audit record. End users intentionally have no
    # generic create permission; only this validated server path can insert it.
    frappe.get_doc(
        {
            "doctype": "Synora Agent Run",
            "name": run_id,
            "initiator": initiator,
            "goal": goal,
            "execution_mode": execution_mode,
            "time_window_days": time_window_days,
            "company_scope": company,
            "warehouse_scope": warehouse,
            "capability_digest": _digest(run_id, capability),
            "capability_audience": CAPABILITY_AUDIENCE,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "workflow_expires_at": workflow_expires_at,
            "revoked": 0,
            "status": "ACTIVE",
            "run_state": "CREATED",
            "state_version": 1,
            "correlation_id": correlation_id,
        }
    ).insert(ignore_permissions=True)
    return {
        "run_id": run_id,
        "capability": capability,
        "audience": CAPABILITY_AUDIENCE,
        "expires_at": str(expires_at),
        "workflow_expires_at": str(workflow_expires_at) if workflow_expires_at else None,
        "state_version": 1,
        "run_state": "CREATED",
        "execution_mode": execution_mode,
    }


def revoke_run(run_id: str, correlation_id: str) -> dict[str, str | int]:
    if not frappe.db.exists("Synora Agent Run", run_id):
        raise GatewayFault("RUN_REJECTED", "run is not available", 404)
    run = frappe.get_doc("Synora Agent Run", run_id)
    actor = frappe.session.user
    if actor != run.initiator and "System Manager" not in frappe.get_roles(actor):
        raise GatewayFault("PERMISSION_DENIED", "run is not available", 403)
    if run.status != "ACTIVE" or run.revoked:
        raise GatewayFault("CONFLICT", "run is not active", 409)
    run.flags.synora_revocation = True
    run.revoked = 1
    run.status = "REVOKED"
    run.revoked_at = now_datetime()
    run.revoked_by = actor
    run.revocation_correlation_id = correlation_id
    run.state_version += 1
    run.save(ignore_permissions=True)
    return {"run_id": run.name, "status": run.status, "state_version": run.state_version}


def rotate_run_capability(run: Any) -> str:
    """Issue one fresh short-lived capability for the internal Agent request."""
    if (
        getattr(run, "status", None) != "ACTIVE"
        or getattr(run, "revoked", False)
        or getattr(run, "run_state", None) != "ANALYZING"
    ):
        raise GatewayFault("CONFLICT", "run is not active", 409)
    if workflow_expired(run):
        raise GatewayFault("CONFLICT", "workflow has expired", 409)
    capability = secrets.token_urlsafe(32)
    issued_at = now_datetime()
    expires_at = issued_at + CAPABILITY_TTL
    run.flags.synora_capability_rotation = True
    run.capability_digest = _digest(run.name, capability)
    run.issued_at = issued_at
    run.expires_at = expires_at
    run.save(ignore_permissions=True)
    return capability


def cancel_run(run_id: str, correlation_id: str) -> dict[str, str | int]:
    """发起人取消分析: run_state CREATED/ANALYZING -> CANCELLED 并撤销 capability。

    取消必须通过确定性状态机校验 (fail-closed); 取消后 capability 同步失效,
    避免已取消的分析继续执行工具调用。
    """
    if not frappe.db.exists("Synora Agent Run", run_id):
        raise GatewayFault("RUN_REJECTED", "run is not available", 404)
    run = frappe.get_doc("Synora Agent Run", run_id)
    actor = frappe.session.user
    if actor != run.initiator and "System Manager" not in frappe.get_roles(actor):
        raise GatewayFault("PERMISSION_DENIED", "run is not available", 403)
    if run.execution_mode == "PLAN_EXECUTE" and workflow_expired(run):
        return _expire_loaded_run(run, correlation_id, actor)
    if run.run_state not in CANCELLABLE_STATES or run.status != "ACTIVE" or run.revoked:
        raise GatewayFault("CONFLICT", "run cannot be cancelled", 409)
    validate_transition(run.run_state, "CANCELLED")
    run.flags.synora_state_change = True
    run.flags.synora_revocation = True
    run.run_state = "CANCELLED"
    run.revoked = 1
    run.status = "REVOKED"
    run.revoked_at = now_datetime()
    run.revoked_by = actor
    run.revocation_correlation_id = correlation_id
    run.state_version += 1
    run.save(ignore_permissions=True)
    return {
        "run_id": run.name,
        "run_state": run.run_state,
        "status": run.status,
        "state_version": run.state_version,
    }


def workflow_expired(run: Any, *, at: Any | None = None) -> bool:
    """Return true only for a PLAN_EXECUTE row past its 24-hour deadline."""
    if getattr(run, "execution_mode", None) != "PLAN_EXECUTE":
        return False
    deadline = getattr(run, "workflow_expires_at", None)
    if not deadline:
        return True
    current = at or now_datetime()
    try:
        return bool(get_datetime(deadline) <= current)
    except TypeError:
        return True
    except ValueError:
        return True


def _expire_loaded_run(run: Any, correlation_id: str, actor: str) -> dict[str, str | int]:
    if run.status != "ACTIVE" or run.revoked:
        raise GatewayFault("CONFLICT", "run is not active", 409)
    if run.run_state not in {"CREATED", "ANALYZING"}:
        raise GatewayFault("CONFLICT", "run cannot expire", 409)
    validate_transition(run.run_state, "EXPIRED")
    run.flags.synora_state_change = True
    run.flags.synora_revocation = True
    run.run_state = "EXPIRED"
    run.revoked = 1
    run.status = "EXPIRED"
    run.revoked_at = now_datetime()
    run.revoked_by = actor
    run.revocation_correlation_id = correlation_id
    run.state_version += 1
    run.save(ignore_permissions=True)
    return {
        "run_id": run.name,
        "run_state": run.run_state,
        "status": run.status,
        "state_version": run.state_version,
    }


def expire_run(run_id: str, correlation_id: str) -> dict[str, str | int]:
    """Authoritatively expire an overdue PLAN_EXECUTE Run."""
    if not frappe.db.exists("Synora Agent Run", run_id):
        raise GatewayFault("RUN_REJECTED", "run is not available", 404)
    run = frappe.get_doc("Synora Agent Run", run_id)
    actor = frappe.session.user
    if actor != run.initiator and "System Manager" not in frappe.get_roles(actor):
        raise GatewayFault("PERMISSION_DENIED", "run is not available", 403)
    if run.execution_mode != "PLAN_EXECUTE" or not workflow_expired(run):
        raise GatewayFault("CONFLICT", "workflow has not expired", 409)
    return _expire_loaded_run(run, correlation_id, actor)


def resolve_run(run_id: str, capability: str) -> RunContext:
    if not frappe.db.exists("Synora Agent Run", run_id):
        raise GatewayFault("RUN_REJECTED", "run capability is invalid", 401)
    run = frappe.get_doc("Synora Agent Run", run_id)
    valid = (
        run.status == "ACTIVE"
        and not run.revoked
        and run.capability_audience == CAPABILITY_AUDIENCE
        and get_datetime(run.expires_at) > now_datetime()
        and not workflow_expired(run)
        and hmac.compare_digest(run.capability_digest, _digest(run_id, capability))
    )
    if not valid:
        raise GatewayFault("RUN_REJECTED", "run capability is invalid", 401)
    return RunContext(
        run_id=run.name,
        initiator=run.initiator,
        company=run.company_scope,
        warehouse=run.warehouse_scope or None,
        state_version=run.state_version,
    )


def recheck_run_scope(run: RunContext, required_doctypes: tuple[str, ...]) -> None:
    enabled = frappe.db.get_value("User", run.initiator, "enabled")
    if not enabled:
        raise GatewayFault("PERMISSION_DENIED", "requested resource is not available", 403)
    if run.company not in frappe.get_list(
        "Company", pluck="name", filters={"name": run.company}, limit=1, user=run.initiator
    ):
        raise GatewayFault("PERMISSION_DENIED", "requested resource is not available", 403)
    if run.warehouse and run.warehouse not in frappe.get_list(
        "Warehouse",
        pluck="name",
        filters={"name": run.warehouse, "company": run.company, "disabled": 0},
        limit=1,
        user=run.initiator,
    ):
        raise GatewayFault("PERMISSION_DENIED", "requested resource is not available", 403)
    if any(
        not frappe.has_permission(doctype, "read", user=run.initiator)
        for doctype in required_doctypes
    ):
        raise GatewayFault("PERMISSION_DENIED", "requested resource is not available", 403)


def record_gateway_audit(
    run: RunContext,
    tool_name: str,
    tool_version: str,
    correlation_id: str,
    outcome: str,
    error_code: str | None = None,
) -> None:
    frappe.get_doc(
        {
            "doctype": "Synora Gateway Audit",
            "run": run.run_id,
            "initiator": run.initiator,
            "company_scope": run.company,
            "warehouse_scope": run.warehouse,
            "tool_name": tool_name,
            "tool_version": tool_version,
            "correlation_id": correlation_id,
            "outcome": outcome,
            "error_code": error_code,
            "occurred_at": now_datetime(),
        }
    ).insert(ignore_permissions=True)
