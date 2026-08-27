import json
from typing import Any
from uuid import uuid4

import frappe
from frappe.recorder import do_not_record
from frappe.utils import cint, get_datetime, now_datetime

from synora_agentic_erp.agent.invocation import complete_invocation, reserve_invocation
from synora_agentic_erp.agent.service import (
    _AGENT_EVENT_TYPES,
    _safe_trace_value,
    _validate_trace_semantics,
    cancel_workflow_runtime,
    get_workflow_status,
    resume_plan_execute_run,
)
from synora_agentic_erp.agent.service import (
    analyze_run as analyze_server_run,
)
from synora_agentic_erp.agent.service import (
    plan_run as plan_server_run,
)
from synora_agentic_erp.gateway.contract import (
    SCHEMA_VERSION,
    GatewayFault,
    bounded_text,
    canonical_uuid,
    error_response,
    optional_text,
    parse_request,
    positive_int,
)
from synora_agentic_erp.gateway.contract import (
    correlation_id as validate_correlation_id,
)
from synora_agentic_erp.gateway.registry import dispatch
from synora_agentic_erp.gateway.security import (
    EXECUTION_MODES,
    record_gateway_audit,
    reject_mixed_user_credentials,
    require_capability_only_request,
    resolve_run,
)
from synora_agentic_erp.gateway.security import (
    cancel_run as cancel_server_run,
)
from synora_agentic_erp.gateway.security import (
    issue_run as create_run,
)
from synora_agentic_erp.gateway.security import (
    revoke_run as revoke_server_run,
)

# 未认证入口 (execute, allow_guest) 的安全事件日志预算: 每分钟最多记录条数,
# 用于防日志放大; 超出预算的事件静默丢弃。
SECURITY_EVENT_BUDGET = 60

# P3.1 决策包批准的产品语义。
GOAL_MAX_LENGTH = 1000
DEFAULT_TIME_WINDOW_DAYS = 90
MAX_TIME_WINDOW_DAYS = 365
TRACE_DEFAULT_LIMIT = 50
TRACE_MAX_LIMIT = 200
TRACE_MAX_OFFSET = 10_000


def _reject_trace_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_trace_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parse_trace_json(raw: object, expected: type[list[Any]] | type[dict[str, Any]]) -> Any:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 2_000_000:
        raise ValueError("trace payload is invalid")
    value = json.loads(
        raw,
        parse_constant=_reject_trace_json_constant,
        object_pairs_hook=_unique_trace_json_pairs,
    )
    if not isinstance(value, expected):
        raise ValueError("trace payload is invalid")
    return value


def _safe_trace_stop_reason(raw: object) -> dict[str, object]:
    parsed = _parse_trace_json(raw, dict)
    code = parsed.get("code")
    if not isinstance(code, str) or code not in {
        "FINAL_ANSWER",
        "MAX_STEPS",
        "REPEATED_CALL",
        "NO_PROGRESS",
        "TOKEN_BUDGET",
        "COST_BUDGET",
        "WALL_TIME_BUDGET",
        "CANCELLED",
        "TOOL_NOT_ALLOWED",
        "TOOL_FREQUENCY",
        "INVALID_TOOL_ARGS",
        "TOOL_ERROR",
        "MODEL_ERROR",
        "UNSUPPORTED_FINAL_ANSWER",
    }:
        raise ValueError("trace stop reason is invalid")
    return _safe_trace_value(parsed)  # type: ignore[return-value]


def _safe_trace_events(
    raw: object, run_id: str, *, expected_stop_code: str | None = None
) -> list[dict[str, object]]:
    events = _parse_trace_json(raw, list)
    if len(events) > 512:
        raise ValueError("trace event count is invalid")
    safe_events: list[dict[str, object]] = []
    for expected_sequence, event in enumerate(events, 1):
        if not isinstance(event, dict):
            raise ValueError("trace event is invalid")
        if (
            event.get("schema_version") != "1"
            or event.get("payload_version") != "1"
            or event.get("run_id") != run_id
            or event.get("sequence") != expected_sequence
            or event.get("event_type") not in _AGENT_EVENT_TYPES
            or not isinstance(event.get("timestamp"), str)
            or not isinstance(event.get("payload"), dict)
        ):
            raise ValueError("trace event ordering or shape is invalid")
        safe_events.append(
            {
                "schema_version": "1",
                "run_id": run_id,
                "sequence": expected_sequence,
                "event_type": event["event_type"],
                "timestamp": str(event["timestamp"])[:64],
                "payload_version": "1",
                "payload": _safe_trace_value(event["payload"]),
            }
        )
    stopped = events[-1].get("payload") if events and isinstance(events[-1], dict) else None
    actual_stop_code = stopped.get("code") if isinstance(stopped, dict) else None
    stop_code = expected_stop_code or actual_stop_code
    if not isinstance(stop_code, str):
        raise ValueError("trace stop reason is invalid")
    _validate_trace_semantics(events, stop_code=stop_code)
    return safe_events


def _latest_agent_trace(run_id: str) -> dict[str, Any] | None:
    """Return a redacted summary; callers must authorize the parent Run first."""
    try:
        attempts = frappe.get_all(
            "Synora Agent Trace Attempt",
            filters={"run": run_id},
            fields=[
                "attempt",
                "mode",
                "provider",
                "model",
                "prompt_schema_version",
                "tool_schema_version",
                "events_count",
                "stop_reason",
                "prompt_tokens",
                "completion_tokens",
                "reasoning_tokens",
                "cost_microusd",
                "elapsed_ms",
                "status",
                "correlation_id",
                "creation",
            ],
            order_by="attempt desc, creation desc",
            limit=1,
            ignore_permissions=True,
        )
    except Exception:
        # During an older site migration the new Trace table may not exist yet;
        # keep existing deterministic Run reads available without exposing detail.
        return None
    if not attempts:
        return None
    doc = attempts[0]
    try:
        stop_reason = _safe_trace_stop_reason(doc.stop_reason)
    except Exception:
        stop_reason = {"code": "TRACE_INVALID", "detail": "trace payload unavailable"}
    return {
        "attempt": int(doc.attempt or 0),
        "mode": str(doc.mode or "AGENT"),
        "provider": str(doc.provider or "")[:120],
        "model": str(doc.model or "")[:200],
        "prompt_schema_version": str(doc.prompt_schema_version or "1"),
        "tool_schema_version": str(doc.tool_schema_version or "1"),
        "events_count": int(doc.events_count or 0),
        "stop_reason": stop_reason,
        "usage": {
            "prompt_tokens": int(doc.prompt_tokens or 0),
            "completion_tokens": int(doc.completion_tokens or 0),
            "reasoning_tokens": int(doc.reasoning_tokens or 0),
            "cost_microusd": int(doc.cost_microusd or 0),
        },
        "elapsed_ms": int(doc.elapsed_ms or 0),
        "status": str(doc.status or "FAILED"),
        "correlation_id": str(doc.correlation_id or "")[:36],
        "created_at": str(doc.creation or ""),
    }


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
    try:
        if not _security_event_budget_allowed():
            return
        ip = getattr(frappe.local, "request_ip", None) or "-"
        frappe.log_error(
            message=(
                "synora gateway security event "
                f"(code={code}, correlation={correlation_id}, ip={ip})"
            ),
            title="Synora Gateway Security Event",
        )
    except Exception:
        # 安全事件日志本身不能成为失败路径 (缓存/日志故障时静默降级, 不影响请求)。
        pass


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
    company: str,
    goal: str,
    warehouse: str | None = None,
    time_window_days: int | None = None,
    correlation_id: str | None = None,
    execution_mode: str | None = None,
) -> dict[str, Any]:
    safe_correlation_id: str | None = None
    try:
        reject_mixed_user_credentials()
        if correlation_id is None:
            correlation_id = str(uuid4())
        safe_correlation_id = validate_correlation_id(correlation_id)
        safe_company = bounded_text(company, "company")
        # P3.1 批准: goal 必填, 服务端 fail-closed 校验 1000 字符上限;
        # 空白字符组成的目标视为空目标, 明确拒绝而非自动猜测。
        safe_goal = bounded_text(goal, "goal", GOAL_MAX_LENGTH)
        if not safe_goal.strip():
            raise GatewayFault("INVALID_INPUT", "goal is invalid")
        safe_warehouse = optional_text(warehouse, "warehouse")
        # P3.1 批准: time_window 缺省 = 当前库存 + 在途 + 未来 90 天需求。
        if time_window_days is None:
            safe_days = DEFAULT_TIME_WINDOW_DAYS
        else:
            safe_days = positive_int(time_window_days, "time_window_days", MAX_TIME_WINDOW_DAYS)
            if safe_days == 0:
                raise GatewayFault("INVALID_INPUT", "time_window_days is invalid")
        safe_execution_mode = "DETERMINISTIC" if execution_mode is None else execution_mode
        if not isinstance(safe_execution_mode, str) or safe_execution_mode not in EXECUTION_MODES:
            raise GatewayFault("INVALID_INPUT", "execution_mode is invalid")
        run = create_run(
            safe_company,
            safe_goal,
            safe_warehouse,
            safe_days,
            safe_correlation_id,
            safe_execution_mode,
        )
        if safe_execution_mode == "PLAN_EXECUTE":
            # A durable workflow receives a fresh capability only when a
            # Runtime segment starts/resumes; never expose the issue-time
            # capability in the public response.
            run = {key: value for key, value in run.items() if key != "capability"}
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
def cancel_run(run_id: str, correlation_id: str) -> dict[str, Any]:
    """发起人取消分析 (run_state CREATED/ANALYZING -> CANCELLED)。"""
    safe_correlation_id: str | None = None
    try:
        reject_mixed_user_credentials()
        safe_correlation_id = validate_correlation_id(correlation_id)
        safe_run_id = canonical_uuid(run_id, "run_id")
        workflow_revision: int | None = None
        is_workflow = False
        if frappe.db.exists("Synora Agent Run", safe_run_id):
            candidate = frappe.get_doc("Synora Agent Run", safe_run_id)
            is_workflow = candidate.execution_mode == "PLAN_EXECUTE"
            if is_workflow and candidate.run_state == "ANALYZING":
                try:
                    status = get_workflow_status(safe_run_id)
                    workflow = status.get("workflow", {})
                    if isinstance(workflow, dict) and isinstance(workflow.get("revision"), int):
                        workflow_revision = workflow["revision"]
                except GatewayFault:
                    # Frappe cancellation remains authoritative when Runtime
                    # status is unavailable; cleanup below is best effort.
                    workflow_revision = None
        run = cancel_server_run(safe_run_id, safe_correlation_id)
        if is_workflow and workflow_revision is not None:
            try:
                cancel_workflow_runtime(safe_run_id, workflow_revision)
            except GatewayFault:
                pass
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
def analyze_run(run_id: str, correlation_id: str) -> dict[str, Any]:
    """触发确定性采购风险分析 (run_state CREATED -> ANALYZING -> PROPOSED)。

    数量、金额、日期和阈值由确定性代码计算 (PRD F-003), 模型不参与。
    """
    safe_correlation_id: str | None = None
    try:
        reject_mixed_user_credentials()
        safe_correlation_id = validate_correlation_id(correlation_id)
        safe_run_id = canonical_uuid(run_id, "run_id")
        result = analyze_server_run(safe_run_id, safe_correlation_id)
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "correlation_id": safe_correlation_id,
            "analysis": result,
        }
    except GatewayFault as fault:
        _set_status(fault.status_code)
        return error_response(fault, safe_correlation_id)


@frappe.whitelist(methods=["POST"])  # type: ignore[untyped-decorator]
@do_not_record  # type: ignore[untyped-decorator]
def resume_run(
    run_id: str,
    correlation_id: str,
    workflow_revision: int,
    interrupt_id: str,
    answer: str,
) -> dict[str, Any]:
    """Consume one current clarification and resume a PLAN_EXECUTE workflow."""
    safe_correlation_id: str | None = None
    try:
        reject_mixed_user_credentials()
        safe_correlation_id = validate_correlation_id(correlation_id)
        safe_run_id = canonical_uuid(run_id, "run_id")
        safe_revision = positive_int(workflow_revision, "workflow_revision", 1_000_000)
        safe_interrupt_id = canonical_uuid(interrupt_id, "interrupt_id")
        safe_answer = bounded_text(answer, "answer", 4_000)
        result = resume_plan_execute_run(
            safe_run_id,
            safe_correlation_id,
            safe_revision,
            safe_interrupt_id,
            safe_answer,
        )
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "correlation_id": safe_correlation_id,
            "analysis": result,
        }
    except GatewayFault as fault:
        _set_status(fault.status_code)
        return error_response(fault, safe_correlation_id)


@frappe.whitelist(methods=["GET"])  # type: ignore[untyped-decorator]
@do_not_record  # type: ignore[untyped-decorator]
def get_run_workflow(run_id: str) -> dict[str, Any]:
    """Load only the redacted Runtime workflow state for an authorized Run."""
    try:
        safe_run_id = canonical_uuid(run_id, "run_id")
        result = get_workflow_status(safe_run_id)
        return {"ok": True, "schema_version": SCHEMA_VERSION, **result}
    except GatewayFault as fault:
        _set_status(fault.status_code)
        return error_response(fault, None)


@frappe.whitelist(methods=["POST"])  # type: ignore[untyped-decorator]
@do_not_record  # type: ignore[untyped-decorator]
def plan_run(run_id: str, correlation_id: str) -> dict[str, Any]:
    """生成可解释只读计划 (PROPOSED -> SUCCEEDED, 只读无写入)。"""
    safe_correlation_id: str | None = None
    try:
        reject_mixed_user_credentials()
        safe_correlation_id = validate_correlation_id(correlation_id)
        safe_run_id = canonical_uuid(run_id, "run_id")
        result = plan_server_run(safe_run_id, safe_correlation_id)
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "correlation_id": safe_correlation_id,
            "plan": result,
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


def _is_system_manager() -> bool:
    return "System Manager" in frappe.get_roles(frappe.session.user)


def _visible_run_filter() -> dict[str, str]:
    """Runs 列表/详情只对发起人本人 (或 System Manager) 可见。"""
    return {} if _is_system_manager() else {"initiator": frappe.session.user}


def _run_summary(run: Any) -> dict[str, Any]:
    """Run 摘要; 展示层归一化: capability TTL 已过的 ACTIVE Run 显示为 EXPIRED。"""
    execution_mode = getattr(run, "execution_mode", "DETERMINISTIC") or "DETERMINISTIC"
    capability_expired = (
        run.status == "ACTIVE"
        and not run.revoked
        and get_datetime(run.expires_at) <= now_datetime()
    )
    workflow_deadline = getattr(run, "workflow_expires_at", None)
    workflow_expired = (
        execution_mode == "PLAN_EXECUTE"
        and bool(workflow_deadline)
        and get_datetime(workflow_deadline) <= now_datetime()
    )
    expired = capability_expired or workflow_expired or run.run_state == "EXPIRED"
    agent_trace = _latest_agent_trace(run.name) if execution_mode == "AGENT" else None
    return {
        "run_id": run.name,
        "goal": run.goal,
        "execution_mode": execution_mode,
        "agent_status": (agent_trace or {}).get("status", "NOT_STARTED")
        if execution_mode == "AGENT"
        else None,
        "agent_trace": agent_trace,
        "run_state": "EXPIRED" if expired else run.run_state,
        "status": run.status,
        "initiator": run.initiator,
        "company_scope": run.company_scope,
        "warehouse_scope": run.warehouse_scope or None,
        "time_window_days": run.time_window_days,
        "expires_at": str(run.expires_at),
        "workflow_expires_at": str(workflow_deadline) if workflow_deadline else None,
        "workflow_status": None,
        "created_at": str(run.creation),
        "expired": expired,
    }


@frappe.whitelist(methods=["GET"])  # type: ignore[untyped-decorator]
@do_not_record  # type: ignore[untyped-decorator]
def list_runs(limit: int | None = None, offset: int | None = None) -> dict[str, Any]:
    """Runs 页面: 当前用户 (或 System Manager) 的 Run 历史, 最新在前, 支持分页。

    默认返回最近 50 条; 分页通过 limit/offset 控制 (limit 上限 200), 返回
    total 供前端计算页数。limit/offset 非法时按默认值处理 (不 fail 请求)。
    """
    if frappe.session.user == "Guest":
        _set_status(401)
        return error_response(
            GatewayFault("AUTHENTICATION_REQUIRED", "authenticated user required", 401)
        )
    safe_limit = 50
    if limit is not None:
        try:
            parsed = positive_int(limit, "limit", 200)
            if parsed > 0:
                safe_limit = parsed
        except GatewayFault:
            safe_limit = 50
    safe_offset = 0
    if offset is not None:
        try:
            parsed = positive_int(offset, "offset", 10_000)
            safe_offset = parsed
        except GatewayFault:
            safe_offset = 0
    total = frappe.db.count("Synora Agent Run", filters=_visible_run_filter())
    runs = frappe.get_all(
        "Synora Agent Run",
        filters=_visible_run_filter(),
        fields=[
            "name",
            "goal",
            "execution_mode",
            "run_state",
            "status",
            "revoked",
            "expires_at",
            "workflow_expires_at",
            "initiator",
            "company_scope",
            "warehouse_scope",
            "time_window_days",
            "creation",
        ],
        order_by="creation desc",
        limit=safe_limit,
        offset=safe_offset,
    )
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "runs": [_run_summary(run) for run in runs],
        "count": len(runs),
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
    }


@frappe.whitelist(methods=["GET"])  # type: ignore[untyped-decorator]
@do_not_record  # type: ignore[untyped-decorator]
def get_run(run_id: str) -> dict[str, Any]:
    """Run 详情: 仅发起人 (或 System Manager) 可读; 不泄露他人 Run 存在性。"""
    try:
        safe_run_id = canonical_uuid(run_id, "run_id")
    except GatewayFault as fault:
        _set_status(fault.status_code)
        return error_response(fault, None)
    if not frappe.db.exists("Synora Agent Run", safe_run_id):
        _set_status(404)
        return error_response(GatewayFault("RUN_REJECTED", "run is not available", 404))
    run = frappe.get_doc("Synora Agent Run", safe_run_id)
    if run.initiator != frappe.session.user and not _is_system_manager():
        _set_status(404)
        return error_response(GatewayFault("RUN_REJECTED", "run is not available", 404))
    analyses = frappe.get_all(
        "Synora Item Analysis",
        filters={"run": safe_run_id},
        fields=[
            "name",
            "item_code",
            "risk",
            "actual_qty",
            "demand_qty",
            "incoming_qty",
            "open_mr_qty",
            "net_position",
            "shortage_qty",
            "unknowns",
            "creation",
        ],
        order_by="item_code asc",
        # run 归属已按发起人校验, 子记录读取统一不看角色权限。
        ignore_permissions=True,
    )
    plans = frappe.get_all(
        "Synora Run Plan",
        filters={"run": safe_run_id},
        fields=[
            "plan_json",
            "summary",
            "enhanced_text",
            "provider",
            "prompt_tokens",
            "completion_tokens",
            "reasoning_tokens",
            "elapsed_ms",
            "fallback_reason",
            "creation",
        ],
        order_by="creation desc",
        limit=1,
        ignore_permissions=True,
    )
    plan = None
    if plans:
        try:
            plan = frappe.parse_json(plans[0].plan_json)
        except ValueError:
            plan = None
        if isinstance(plan, dict):
            plan["enhanced_text"] = plans[0].enhanced_text
            plan["evidence"] = {
                "provider": plans[0].provider,
                "prompt_tokens": plans[0].prompt_tokens,
                "completion_tokens": plans[0].completion_tokens,
                "reasoning_tokens": plans[0].reasoning_tokens,
                "elapsed_ms": plans[0].elapsed_ms,
                "fallback_reason": plans[0].fallback_reason,
            }
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "run": _run_summary(run),
        "analyses": analyses,
        "plan": plan,
    }


@frappe.whitelist(methods=["GET"])  # type: ignore[untyped-decorator]
@do_not_record  # type: ignore[untyped-decorator]
def get_run_trace(
    run_id: str,
    limit: int | None = None,
    offset: int | None = None,
) -> dict[str, Any]:
    """读取当前用户有权访问的最近 Agent Trace 事件页。"""
    try:
        safe_run_id = canonical_uuid(run_id, "run_id")
    except GatewayFault as fault:
        _set_status(fault.status_code)
        return error_response(fault, None)
    if frappe.session.user == "Guest" or not frappe.db.exists("Synora Agent Run", safe_run_id):
        _set_status(404)
        return error_response(GatewayFault("RUN_REJECTED", "run is not available", 404))
    run = frappe.get_doc("Synora Agent Run", safe_run_id)
    if run.initiator != frappe.session.user and not _is_system_manager():
        _set_status(404)
        return error_response(GatewayFault("RUN_REJECTED", "run is not available", 404))
    safe_limit = TRACE_DEFAULT_LIMIT
    if limit is not None:
        try:
            safe_limit = positive_int(limit, "limit", TRACE_MAX_LIMIT)
            if safe_limit == 0:
                safe_limit = TRACE_DEFAULT_LIMIT
        except GatewayFault:
            safe_limit = TRACE_DEFAULT_LIMIT
    safe_offset = 0
    if offset is not None:
        try:
            safe_offset = positive_int(offset, "offset", TRACE_MAX_OFFSET)
        except GatewayFault:
            safe_offset = 0
    try:
        attempts = frappe.get_all(
            "Synora Agent Trace Attempt",
            filters={"run": safe_run_id},
            fields=[
                "name",
                "attempt",
                "mode",
                "provider",
                "model",
                "prompt_schema_version",
                "tool_schema_version",
                "events_json",
                "events_count",
                "stop_reason",
                "prompt_tokens",
                "completion_tokens",
                "reasoning_tokens",
                "cost_microusd",
                "elapsed_ms",
                "status",
                "correlation_id",
                "creation",
            ],
            order_by="attempt desc, creation desc",
            limit=1,
            ignore_permissions=True,
        )
        if not attempts:
            return {
                "ok": True,
                "schema_version": SCHEMA_VERSION,
                "run_id": safe_run_id,
                "trace": None,
            }
        doc = attempts[0]
        stop_reason = _safe_trace_stop_reason(doc.stop_reason)
        events = _safe_trace_events(
            doc.events_json, safe_run_id, expected_stop_code=str(stop_reason["code"])
        )
        summary = _latest_agent_trace(safe_run_id)
        if summary is None:
            raise ValueError("trace summary is unavailable")
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "run_id": safe_run_id,
            "trace": {
                **summary,
                "events": events[safe_offset : safe_offset + safe_limit],
                "count": len(events[safe_offset : safe_offset + safe_limit]),
                "total": len(events),
                "limit": safe_limit,
                "offset": safe_offset,
            },
        }
    except Exception:
        _set_status(500)
        return error_response(GatewayFault("ERP_ERROR", "trace is unavailable", 500))


@frappe.whitelist(methods=["GET"])  # type: ignore[untyped-decorator]
@do_not_record  # type: ignore[untyped-decorator]
def available_scope() -> dict[str, Any]:
    """New Run 页面: 当前用户可访问的公司及其中未停用仓库 (权限过滤)。"""
    if frappe.session.user == "Guest":
        _set_status(401)
        return error_response(
            GatewayFault("AUTHENTICATION_REQUIRED", "authenticated user required", 401)
        )
    viewer = frappe.session.user
    companies = frappe.get_list("Company", pluck="name", order_by="name", user=viewer)
    scope = [
        {
            "company": company,
            "warehouses": frappe.get_list(
                "Warehouse",
                pluck="name",
                filters={"company": company, "disabled": 0},
                order_by="name",
                user=viewer,
            ),
        }
        for company in companies
    ]
    return {"ok": True, "schema_version": SCHEMA_VERSION, "scope": scope}


@frappe.whitelist(allow_guest=True, methods=["POST"])  # type: ignore[untyped-decorator]
@do_not_record  # type: ignore[untyped-decorator]
def execute(**payload: Any) -> dict[str, Any]:
    safe_correlation_id: str | None = None
    request = None
    run = None
    reservation = None
    try:
        require_capability_only_request()
        # Frappe RPC 路由会把请求路径注入 form_dict.cmd (frappe/api/v1.py),
        # 该键不属于 Gateway 契约, 必须在信任边界剥离后再做严格解析。
        payload.pop("cmd", None)
        request = parse_request(payload)
        safe_correlation_id = request.correlation_id
        run = resolve_run(request.run_id, request.capability)
        reservation = reserve_invocation(request, run)
        if reservation is not None and reservation.cached_response is not None:
            record_gateway_audit(
                run,
                request.tool.name,
                request.tool.version,
                request.correlation_id,
                "CACHED",
            )
            return reservation.cached_response
        if reservation is not None:
            # The STARTED row is the durable boundary before a read-only ERP
            # handler dispatch.  If the process dies after this commit, the
            # next request must see an uncertain invocation and not replay it.
            frappe.db.commit()
        result = dispatch(request, run)
        if reservation is not None:
            result = complete_invocation(reservation, result)
            frappe.db.commit()
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
        if reservation is not None and reservation.cached_response is None:
            # Preserve STARTED on every dispatch failure; it represents an
            # unresolved window and is intentionally not auto-replayed.
            try:
                frappe.db.commit()
            except Exception:
                pass
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
        if reservation is not None and reservation.cached_response is None:
            try:
                frappe.db.commit()
            except Exception:
                pass
        _set_status(erp_fault.status_code)
        return error_response(erp_fault, safe_correlation_id)
