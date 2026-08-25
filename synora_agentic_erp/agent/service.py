"""P3.3 确定性采购分析编排 (Frappe 侧)。

数据获取复用 Phase 2 typed 只读工具 (dispatch + recheck_run_scope),
数量/日期/阈值计算全部委托 agent.analysis 纯函数; LLM 不参与。
分析完成 run_state: CREATED -> ANALYZING -> PROPOSED (SPEC §8.1)。
"""

from datetime import date
from decimal import Decimal
from typing import Any

import frappe

from synora_agentic_erp.agent.analysis import (
    NEEDS_INPUT,
    DemandLine,
    IncomingLine,
    ItemAnalysis,
    ItemInput,
    analyze_item,
    horizon_date,
)
from synora_agentic_erp.agent.state_machine import validate_transition
from synora_agentic_erp.gateway.contract import (
    GatewayFault,
    GatewayRequest,
    ToolCall,
)
from synora_agentic_erp.gateway.registry import dispatch
from synora_agentic_erp.gateway.security import RunContext

# 单个 Run 分析的最大 item 数 (超出返回 RESULT_LIMIT, 防止一次分析过慢)。
# ponytail: 固定上限; 若真实场景超出再引入分批/后台任务。
MAX_ANALYSIS_ITEMS = 200
_TOOL_PAGE_SIZE = 50


def _run_context(run: Any) -> RunContext:
    return RunContext(
        run_id=run.name,
        initiator=run.initiator,
        company=run.company_scope,
        warehouse=run.warehouse_scope or None,
        state_version=run.state_version,
    )


def _set_run_state(run: Any, target: str) -> None:
    validate_transition(run.run_state, target)
    run.flags.synora_state_change = True
    run.run_state = target
    run.state_version += 1
    run.save(ignore_permissions=True)


def _call_tool(
    ctx: RunContext, name: str, tool_input: dict[str, object], correlation_id: str
) -> dict[str, Any]:
    request = GatewayRequest(
        run_id=ctx.run_id,
        # dispatch 不校验 capability; 权限由 recheck_run_scope 以 initiator 身份重检
        capability="server-analyze",
        correlation_id=correlation_id,
        tool=ToolCall(name=name, version="1", input=tool_input),
    )
    return dispatch(request, ctx)


def _collect_rows(
    ctx: RunContext, name: str, tool_input: dict[str, object], correlation_id: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = _call_tool(
            ctx,
            name,
            {**tool_input, "limit": _TOOL_PAGE_SIZE, "offset": offset},
            correlation_id,
        )
        rows.extend(response["data"])
        if not response["page"]["has_more"]:
            break
        offset += _TOOL_PAGE_SIZE
    return rows


def _parse_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _analyze_item(
    ctx: RunContext,
    item_code: str,
    time_window_days: int,
    mr_rows: list[dict[str, Any]],
    po_rows: list[dict[str, Any]],
    correlation_id: str,
) -> ItemAnalysis:
    horizon = horizon_date(date.today(), time_window_days)
    projected_rows = _collect_rows(ctx, "stock.projected", {"item_code": item_code}, correlation_id)
    actual_qty = sum((Decimal(str(row["actual_qty"])) for row in projected_rows), Decimal("0"))

    item_mr = [row for row in mr_rows if row["item_code"] == item_code]
    demand_lines: list[DemandLine] = []
    open_mr_total = Decimal("0")
    missing_schedule_date = False
    for row in item_mr:
        open_mr_total += Decimal(str(row["open_stock_qty"]))
        scheduled = _parse_date(row.get("schedule_date"))
        if scheduled is None:
            missing_schedule_date = True
            continue
        demand_lines.append(DemandLine(Decimal(str(row["open_stock_qty"])), scheduled))

    item_po = [row for row in po_rows if row["item_code"] == item_code]
    incoming_lines: list[IncomingLine] = []
    for row in item_po:
        scheduled = _parse_date(row.get("schedule_date"))
        if scheduled is None:
            missing_schedule_date = True
            continue
        incoming_lines.append(IncomingLine(Decimal(str(row["open_receipt_qty"])), scheduled))

    analysis = analyze_item(
        ItemInput(
            item_code=item_code,
            actual_qty=actual_qty,
            horizon=horizon,
            demand_lines=tuple(demand_lines),
            incoming_lines=tuple(incoming_lines),
            open_mr_qty=open_mr_total,
        )
    )
    if missing_schedule_date:
        # 有需求/供应行缺少 schedule_date: 窗口判定不完整 -> NEEDS_INPUT。
        return ItemAnalysis(
            item_code=item_code,
            risk=NEEDS_INPUT,
            actual_qty=analysis.actual_qty,
            demand_qty=analysis.demand_qty,
            incoming_qty=analysis.incoming_qty,
            open_mr_qty=analysis.open_mr_qty,
            net_position=analysis.net_position,
            shortage_qty=analysis.shortage_qty,
            unknowns=(*analysis.unknowns, "missing_schedule_date"),
        )
    return analysis


def _persist_analysis(run_id: str, analysis: ItemAnalysis, correlation_id: str) -> None:
    frappe.get_doc(
        {
            "doctype": "Synora Item Analysis",
            "run": run_id,
            "item_code": analysis.item_code,
            "risk": analysis.risk,
            "actual_qty": float(analysis.actual_qty),
            "demand_qty": float(analysis.demand_qty),
            "incoming_qty": float(analysis.incoming_qty),
            "open_mr_qty": float(analysis.open_mr_qty),
            "net_position": float(analysis.net_position),
            "shortage_qty": float(analysis.shortage_qty),
            "unknowns": ",".join(analysis.unknowns),
            "correlation_id": correlation_id,
        }
    ).insert(ignore_permissions=True)


def analyze_run(run_id: str, correlation_id: str) -> dict[str, Any]:
    """对 Run 执行确定性采购风险分析 (仅 CREATED 可开始)。"""
    if not frappe.db.exists("Synora Agent Run", run_id):
        raise GatewayFault("RUN_REJECTED", "run is not available", 404)
    run = frappe.get_doc("Synora Agent Run", run_id)
    actor = frappe.session.user
    if actor != run.initiator and "System Manager" not in frappe.get_roles(actor):
        raise GatewayFault("PERMISSION_DENIED", "run is not available", 403)
    if run.run_state != "CREATED":
        raise GatewayFault("CONFLICT", "run is not analyzable", 409)

    ctx = _run_context(run)
    _set_run_state(run, "ANALYZING")

    # 需求源 = 未结 MR 行; 在途源 = 未收货 PO 行; 各拉取一次后按 item 分组。
    mr_rows = _collect_rows(ctx, "material_request.open", {}, correlation_id)
    po_rows = _collect_rows(ctx, "purchase_order.open", {}, correlation_id)
    item_codes = sorted({row["item_code"] for row in mr_rows})
    if len(item_codes) > MAX_ANALYSIS_ITEMS:
        raise GatewayFault("RESULT_LIMIT", "analysis item scope is too large", 422)

    analyses: list[dict[str, object]] = []
    for item_code in item_codes:
        item_analysis = _analyze_item(
            ctx, item_code, run.time_window_days, mr_rows, po_rows, correlation_id
        )
        _persist_analysis(run_id, item_analysis, correlation_id)
        analyses.append(item_analysis.to_dict())

    _set_run_state(run, "PROPOSED")
    return {
        "run_id": run_id,
        "run_state": run.run_state,
        "state_version": run.state_version,
        "items_analyzed": len(analyses),
        "analyses": analyses,
    }
