from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any, cast

from frappe.utils import now_datetime

from synora_agentic_erp.gateway.contract import (
    MAX_PAGE_SIZE,
    SCHEMA_VERSION,
    GatewayFault,
    GatewayRequest,
    InputField,
    JsonScalar,
    ToolResult,
    parse_tool_input,
)
from synora_agentic_erp.gateway.security import RunContext, recheck_run_scope

ToolHandler = Callable[[RunContext, dict[str, JsonScalar]], ToolResult]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    risk: str
    # post-hoc 耗时分类阈值: handler 返回后才比较, 不中断正在执行的 ERP 调用。
    # ERP 永久卡住时由 Runtime 侧 HTTP 超时兜底; 真正的执行截止需进程隔离,
    # 留待首个写操作阶段 (Phase 4) 按需引入。
    timeout_ms: int
    max_page_size: int
    required_doctypes: tuple[str, ...]
    input_fields: dict[str, InputField]
    handler: ToolHandler


_TOOLS: dict[tuple[str, str], ToolSpec] = {}


def register(
    *,
    name: str,
    version: str,
    required_doctypes: tuple[str, ...],
    input_fields: dict[str, InputField],
    timeout_ms: int = 5000,
    max_page_size: int = MAX_PAGE_SIZE,
) -> Callable[[ToolHandler], ToolHandler]:
    def decorator(handler: ToolHandler) -> ToolHandler:
        key = (name, version)
        if key in _TOOLS:
            raise RuntimeError(f"duplicate gateway tool: {name}@{version}")
        _TOOLS[key] = ToolSpec(
            name=name,
            version=version,
            risk="READ",
            timeout_ms=timeout_ms,
            max_page_size=max_page_size,
            required_doctypes=required_doctypes,
            input_fields=input_fields,
            handler=handler,
        )
        return handler

    return decorator


def dispatch(request: GatewayRequest, run: RunContext) -> dict[str, Any]:
    from synora_agentic_erp.gateway import tools as product_tools

    _ = product_tools
    # 超时是"执行完成后的耗时分类" (post-hoc): handler 返回后才比较耗时,
    # 不会中断正在执行的 ERP 查询。永久卡住的上游由 Runtime HTTP 超时兜底。
    started_at = monotonic()
    spec = _TOOLS.get((request.tool.name, request.tool.version))
    if spec is None:
        raise GatewayFault("TOOL_NOT_ALLOWED", "tool name or version is not allowed", 404)
    recheck_run_scope(run, spec.required_doctypes)
    validated_input = parse_tool_input(request.tool.input, spec.input_fields, spec.max_page_size)
    result = spec.handler(run, validated_input)
    if (monotonic() - started_at) * 1000 > spec.timeout_ms:
        raise GatewayFault(
            "TIMEOUT",
            "tool execution exceeded its post-hoc timeout classification budget",
            504,
        )
    limit = cast(int, validated_input["limit"])
    offset = cast(int, validated_input["offset"])
    if len(result.items) > limit + 1:
        raise GatewayFault("ERP_ERROR", "tool exceeded its result limit", 502)
    for row in result.items:
        if not isinstance(row, dict) or any(
            not isinstance(key, str) or isinstance(value, (dict, list, tuple, set))
            for key, value in row.items()
        ):
            raise GatewayFault("ERP_ERROR", "tool returned an invalid result", 502)
    has_more = len(result.items) > limit
    rows = result.items[:limit]
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "run_id": run.run_id,
        "state_version": run.state_version,
        "correlation_id": request.correlation_id,
        "tool": {
            "name": spec.name,
            "version": spec.version,
            "risk": spec.risk,
            "caller_authorization": "FRAPPE_PERMISSION_AND_RUN_SCOPE",
            "timeout_ms": spec.timeout_ms,
            "max_page_size": spec.max_page_size,
        },
        "authorized_scope": {"company": run.company, "warehouse": run.warehouse},
        "snapshot": {
            "captured_at": str(now_datetime()),
            "source_modified_at": result.source_modified_at,
            "frappe_revision": "6a329d068416768ec47ccd3326b9cc95a8d7bf99",
            "erpnext_revision": "11e0ba0a1c45f217e2e73e885f699102d06da325",
        },
        "completeness": {
            "status": "PARTIAL" if result.omissions else "COMPLETE",
            "omissions": result.omissions,
        },
        "page": {
            "offset": offset,
            "limit": limit,
            "returned": len(rows),
            "has_more": has_more,
        },
        "data": rows,
    }
