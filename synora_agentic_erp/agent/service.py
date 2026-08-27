"""P3.3 确定性采购分析编排 (Frappe 侧)。

数据获取复用 Phase 2 typed 只读工具 (dispatch + recheck_run_scope),
数量/日期/阈值计算全部委托 agent.analysis 纯函数; LLM 不参与。
分析完成 run_state: CREATED -> ANALYZING -> PROPOSED (SPEC §8.1)。

P3.5 模型增强 (验收门槛): plan_run 生成确定性计划后, 可选调用 Agent Runtime
sidecar 的 /enhance 端点让模型改写解释文本; 数量/风险分类仍由确定性代码生成,
模型输出经严格校验, 失败 (Runtime 未运行/未配置 provider/校验不过) 一律回退
确定性摘要, 并把 provider/token/耗时/回退原因证据持久化到 Synora Run Plan。
CI (app-test) 无 Runtime 服务: 走回退路径, 不依赖付费真实模型。
"""

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from decimal import Decimal
from time import monotonic
from typing import Any

import frappe
from frappe.utils import get_datetime, now_datetime

from synora_agentic_erp.agent.analysis import (
    NEEDS_INPUT,
    DemandLine,
    IncomingLine,
    ItemAnalysis,
    ItemInput,
    analyze_item,
    horizon_date,
)
from synora_agentic_erp.agent.plan import AnalysisRow, build_plan
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

# Agent Runtime sidecar (本机服务, 不暴露到外部)。默认仅接受本机回环地址;
# Docker host-gateway 需显式开关和令牌 (防 SSRF: 用户输入不能改变目标)。
_RUNTIME_URL_ENV = "SYNORA_RUNTIME_URL"
_RUNTIME_ALLOW_HOST_GATEWAY_ENV = "SYNORA_RUNTIME_ALLOW_HOST_GATEWAY"
_RUNTIME_TOKEN_ENV = "SYNORA_RUNTIME_TOKEN"
_RUNTIME_HOST_GATEWAY = "host.docker.internal"
_RUNTIME_DEFAULT_URL = "http://127.0.0.1:8001"
# Grok reasoning 模型的简单解释实测约 13 秒, 低推理强度的完整计划实测约 9 秒;
# 仍以 20 秒墙钟上限防止请求悬挂,
# 超时后回退确定性摘要。该上限不是成本上限, 成本仍由 max_tokens/usage 校验控制。
_RUNTIME_TIMEOUT_SECONDS = 20.0
_RUNTIME_RESPONSE_BYTES = 1_000_000
_AGENT_FALLBACK_CODES = {
    "MODEL_ERROR",
    "REPEATED_CALL",
    "NO_PROGRESS",
    "TOKEN_BUDGET",
    "COST_BUDGET",
    "WALL_TIME_BUDGET",
    "MAX_STEPS",
    "TOOL_FREQUENCY",
    "TOOL_NOT_ALLOWED",
    "INVALID_TOOL_ARGS",
    "UNSUPPORTED_FINAL_ANSWER",
}
_AGENT_EVENT_TYPES = {
    "run.started",
    "model.requested",
    "action.proposed",
    "action.validated",
    "action.rejected",
    "tool.started",
    "tool.observed",
    "tool.failed",
    "guard.checked",
    "final.proposed",
    "final.validated",
    "final.rejected",
    "run.stopped",
}
_AGENT_STOP_CODES = _AGENT_FALLBACK_CODES | {"FINAL_ANSWER", "CANCELLED", "TOOL_ERROR"}
_SENSITIVE_TRACE_KEYS = {
    "secret",
    "password",
    "token",
    "capability",
    "authorization",
    "cookie",
    "prompt",
}
_TRACE_SECRET_TEXT = re.compile(
    r"(?i)\b(?:api[_-]?key|bearer|token|secret|password|passwd|capability|authorization|cookie)\b"
    r"\s*[:=]\s*\S+"
)
_NUMBER_TOKEN = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")


def _validate_trace_semantics(
    events: list[dict[str, Any]], *, stop_code: str, final_answer: dict[str, Any] | None = None
) -> dict[str, str]:
    """Validate ownership of evidence and the small terminal trace state machine."""
    if (
        not events
        or events[0].get("event_type") != "run.started"
        or events[-1].get("event_type") != "run.stopped"
    ):
        raise ValueError("trace terminal events are invalid")
    observed: dict[str, str] = {}
    pending: tuple[str, str, int] | None = None
    for event in events[1:-1]:
        kind = event.get("event_type")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("trace payload is invalid")
        if kind == "action.proposed":
            if pending is not None:
                raise ValueError("trace action overlaps")
            raw_tool_name, raw_step = payload.get("tool_name"), payload.get("step")
            if (
                not isinstance(raw_tool_name, str)
                or not isinstance(raw_step, int)
                or isinstance(raw_step, bool)
            ):
                raise ValueError("trace action identity is invalid")
            pending = ("action", raw_tool_name, raw_step)
        elif kind in {"action.validated", "action.rejected"}:
            if kind == "action.validated":
                if (
                    pending is None
                    or pending[0] != "action"
                    or payload.get("tool_name") != pending[1]
                    or payload.get("step") != pending[2]
                ):
                    raise ValueError("trace action transition is invalid")
                pending = ("validated", pending[1], pending[2])
            else:
                pending = None
        elif kind == "tool.started":
            if pending is None or pending[0] != "validated":
                raise ValueError("trace tool transition is invalid")
            if payload.get("tool_name") != pending[1] or payload.get("step") != pending[2]:
                raise ValueError("trace tool ownership is invalid")
            pending = ("tool", pending[1], pending[2])
        elif kind == "tool.observed":
            if pending is None or pending[0] != "tool":
                raise ValueError("trace observation transition is invalid")
            if payload.get("tool_name") != pending[1] or payload.get("step") != pending[2]:
                raise ValueError("trace observation ownership is invalid")
            summary, digest = payload.get("summary"), payload.get("digest")
            if (
                payload.get("ok") is not True
                or not isinstance(summary, str)
                or not isinstance(digest, str)
            ):
                raise ValueError("trace observation is invalid")
            if (
                not re.fullmatch(r"[0-9a-f]{64}", digest)
                or hashlib.sha256(summary.encode()).hexdigest() != digest
            ):
                raise ValueError("trace observation digest is invalid")
            observed[digest] = summary
            pending = None
        elif kind == "tool.failed":
            if pending is None or pending[0] != "tool":
                raise ValueError("trace tool failure transition is invalid")
            if payload.get("tool_name") != pending[1] or payload.get("step") != pending[2]:
                raise ValueError("trace failure ownership is invalid")
            pending = None
        elif kind == "final.proposed":
            if pending is not None:
                raise ValueError("trace final transition is invalid")
            pending = ("final", "", 0)
        elif kind in {"final.validated", "final.rejected"}:
            if kind == "final.validated" and (pending is None or pending[0] != "final"):
                raise ValueError("trace final validation transition is invalid")
            pending = None
        elif kind == "run.stopped":
            raise ValueError("trace has events after terminal")
    stopped = events[-1].get("payload")
    if not isinstance(stopped, dict) or stopped.get("code") != stop_code or pending is not None:
        raise ValueError("trace stop reason is inconsistent")
    if stop_code == "FINAL_ANSWER":
        proposals = [e for e in events if e.get("event_type") == "final.proposed"]
        validated = any(e.get("event_type") == "final.validated" for e in events)
        if not proposals or not validated:
            raise ValueError("final evidence is missing")
        proposal = proposals[-1].get("payload")
        if not isinstance(proposal, dict):
            raise ValueError("final evidence does not match trace")
        final_answer = final_answer or proposal
        if any(
            proposal.get(k) != final_answer.get(k)
            for k in ("status", "summary", "evidence_refs", "unknowns")
        ):
            raise ValueError("final evidence does not match trace")
        refs = final_answer.get("evidence_refs")
        if not isinstance(refs, list) or not refs or not set(refs).issubset(observed):
            raise ValueError("final evidence reference is not observed")
        claimed_numbers = set(_NUMBER_TOKEN.findall(str(final_answer.get("summary", ""))))
        evidence_text = "\n".join(observed[ref] for ref in refs)
        if not claimed_numbers.issubset(set(_NUMBER_TOKEN.findall(evidence_text))):
            raise ValueError("final numeric claim is not observed")
    return observed


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Runtime 返回 3xx 时直接失败, 不把请求转发到未知地址。"""

    def redirect_request(
        self, request: Any, _fp: Any, code: int, _msg: str, headers: Any, _newurl: str
    ) -> None:
        raise urllib.error.HTTPError(
            request.full_url, code, "runtime redirect refused", headers, None
        )


def _runtime_url(path: str) -> str:
    configured = os.environ.get(_RUNTIME_URL_ENV, "").strip().rstrip("/")
    if configured:
        # 严格校验: 仅接受 http(s) 回环地址; 拒绝 userinfo/凭据/任意 host,
        # 防止 urllib 把 userinfo@evil 解析到非本机地址 (SSRF 纵深防御)。
        try:
            parsed = urllib.parse.urlsplit(configured)
        except ValueError as error:
            raise GatewayFault("CONFIG_ERROR", "runtime url is invalid", 500) from error
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            raise GatewayFault("CONFIG_ERROR", "runtime url must be a loopback address", 500)
        loopback_hosts = {"127.0.0.1", "localhost", "::1"}
        if hostname == _RUNTIME_HOST_GATEWAY:
            # Frappe 在 Docker 中运行时, 127.0.0.1 指向 Bench 容器而不是宿主机。
            # 只有开发环境显式打开 host-gateway 且配置内部令牌时才允许该路径;
            # 用户输入不能改变此地址。
            allow_gateway = os.environ.get(_RUNTIME_ALLOW_HOST_GATEWAY_ENV, "").lower()
            if (
                allow_gateway not in {"1", "true", "yes"}
                or not os.environ.get(_RUNTIME_TOKEN_ENV, "").strip()
            ):
                raise GatewayFault(
                    "CONFIG_ERROR", "runtime host gateway requires explicit token config", 500
                )
        elif hostname not in loopback_hosts:
            raise GatewayFault("CONFIG_ERROR", "runtime url must be a loopback address", 500)
        return f"{configured.rstrip('/')}/{path.lstrip('/')}"
    return f"{_RUNTIME_DEFAULT_URL}/{path.lstrip('/')}"


def _runtime_enhance_url() -> str:
    return _runtime_url("enhance")


def _runtime_agent_url() -> str:
    return _runtime_url("agent/execute")


def _enhance_plan_via_runtime(plan: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """调用 Runtime /enhance 生成模型解释; 任何失败回退确定性摘要并记录证据。

    返回 (展示文本, 证据)。证据含 provider/status/prompt_tokens/completion_tokens/
    reasoning_tokens/
    elapsed_ms/fallback_reason; 失败原因只保留类型名与截断消息 (不泄露 key/URL)。
    """
    started = monotonic()

    def fallback(reason: str, status: str = "fallback_error") -> tuple[str, dict[str, Any]]:
        elapsed = int((monotonic() - started) * 1000)
        return str(plan.get("summary", "")), {
            "provider": "runtime",
            "status": status,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
            "elapsed_ms": elapsed,
            "fallback_reason": reason[:200],
        }

    try:
        url = _runtime_enhance_url()
    except GatewayFault as error:
        return fallback(f"runtime config: {error.code}")

    payload = json.dumps({"plan": plan, "provider_name": "byok-runtime"}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    runtime_token = os.environ.get(_RUNTIME_TOKEN_ENV, "").strip()
    if runtime_token:
        headers["X-Synora-Runtime-Token"] = runtime_token
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=_RUNTIME_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read(_RUNTIME_RESPONSE_BYTES))
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        OSError,
        ValueError,
    ) as error:
        # Runtime 未运行 / 超时 / 非法响应: 回退确定性, 不阻塞 plan_run。
        return fallback(f"runtime unavailable: {type(error).__name__}")

    if not isinstance(body, dict):
        # Runtime 返回非对象结构 (list/str/null): 视为异常响应, 回退确定性。
        return fallback("runtime returned a non-object response")

    evidence = body.get("evidence")
    if not isinstance(evidence, dict):
        # evidence 缺失或非对象 (list/str/数字): 回退确定性, 不抛 500。
        return fallback("runtime returned invalid evidence")
    explanation = body.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        return fallback(
            "runtime returned no explanation", status=evidence.get("status", "fallback_error")
        )
    try:
        prompt_tokens = int(evidence.get("prompt_tokens", 0) or 0)
        completion_tokens = int(evidence.get("completion_tokens", 0) or 0)
        reasoning_tokens = int(evidence.get("reasoning_tokens", 0) or 0)
        elapsed_ms = int(evidence.get("elapsed_ms", 0) or 0)
    except Exception:
        # Runtime 返回异常类型: 证据解析失败回退确定性, 不抛 500。
        return fallback("runtime returned malformed evidence")
    return explanation, {
        "provider": str(evidence.get("provider", "runtime"))[:100],
        "status": str(evidence.get("status", "ok"))[:100],
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "reasoning_tokens": reasoning_tokens,
        "elapsed_ms": elapsed_ms,
        "fallback_reason": str(evidence.get("fallback_reason") or "")[:200],
    }


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _safe_trace_value(value: object, *, depth: int = 0) -> object:
    """Bound and redact Runtime data before it can reach a Synora Trace DocType."""
    if depth > 4:
        return "[TRUNCATED]"
    if isinstance(value, str):
        return _TRACE_SECRET_TEXT.sub("[REDACTED]", value[:4_000])
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return "[TRUNCATED]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_safe_trace_value(child, depth=depth + 1) for child in value[:64]]
    if isinstance(value, dict):
        safe: dict[str, object] = {}
        for key, child in list(value.items())[:64]:
            if not isinstance(key, str):
                continue
            normalized_key = key.lower().replace("-", "_")
            if any(marker in normalized_key for marker in _SENSITIVE_TRACE_KEYS):
                safe[key] = "[REDACTED]"
            else:
                safe[key] = _safe_trace_value(child, depth=depth + 1)
        return safe
    return "[TRUNCATED]"


def _safe_trace_text(value: object, maximum: int) -> str:
    text = value if isinstance(value, str) else ""
    return _TRACE_SECRET_TEXT.sub("[REDACTED]", text)[:maximum]


def _runtime_failure_response(
    run_id: str,
    *,
    code: str = "MODEL_ERROR",
    detail: str = "runtime Agent execution was unavailable",
) -> dict[str, Any]:
    timestamp = str(now_datetime())
    event = {
        "schema_version": "1",
        "run_id": run_id,
        "sequence": 1,
        "event_type": "run.started",
        "timestamp": timestamp,
        "payload_version": "1",
        "payload": {"execution_mode": "AGENT", "tool_calling": "native"},
    }
    stopped = {
        "schema_version": "1",
        "run_id": run_id,
        "sequence": 2,
        "event_type": "run.stopped",
        "timestamp": timestamp,
        "payload_version": "1",
        "payload": {"code": code, "step": 0, "detail": detail[:500]},
    }
    return {
        "schema_version": "1",
        "provider": "runtime",
        "model": "",
        "prompt_schema_version": "1",
        "tool_schema_version": "1",
        "result": {
            "schema_version": "1",
            "execution_mode": "AGENT",
            "final_answer": None,
            "stop_reason": {
                "schema_version": "1",
                "code": code,
                "step": 0,
                "detail": detail[:500],
                "budget_snapshot": {
                    "steps": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "reasoning_tokens": 0,
                    "cost_microusd": 0,
                    "elapsed_ms": 0,
                },
            },
            "events": [event, stopped],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "reasoning_tokens": 0,
                "cost_microusd": 0,
            },
            "elapsed_ms": 0,
        },
    }


def _validate_agent_runtime_response(body: object, run_id: str) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) != {
        "schema_version",
        "provider",
        "model",
        "prompt_schema_version",
        "tool_schema_version",
        "result",
    }:
        raise ValueError("runtime response shape is invalid")
    if any(
        body.get(name) != "1"
        for name in ("schema_version", "prompt_schema_version", "tool_schema_version")
    ):
        raise ValueError("runtime response version is invalid")
    if not isinstance(body.get("provider"), str) or not isinstance(body.get("model"), str):
        raise ValueError("runtime provider metadata is invalid")
    result = body.get("result")
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != "1"
        or result.get("execution_mode") != "AGENT"
    ):
        raise ValueError("runtime result is invalid")
    events = result.get("events")
    if not isinstance(events, list) or len(events) > 512:
        raise ValueError("runtime events are invalid")
    safe_events: list[dict[str, object]] = []
    for expected_sequence, event in enumerate(events, 1):
        if not isinstance(event, dict):
            raise ValueError("runtime event is invalid")
        if (
            event.get("schema_version") != "1"
            or event.get("payload_version") != "1"
            or event.get("run_id") != run_id
            or event.get("sequence") != expected_sequence
            or event.get("event_type") not in _AGENT_EVENT_TYPES
            or not isinstance(event.get("timestamp"), str)
            or not isinstance(event.get("payload"), dict)
        ):
            raise ValueError("runtime event ordering or shape is invalid")
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
    if not safe_events or safe_events[0]["event_type"] != "run.started":
        raise ValueError("runtime trace must start with run.started")
    if safe_events[-1]["event_type"] != "run.stopped":
        raise ValueError("runtime trace must end with run.stopped")
    stop_reason = result.get("stop_reason")
    if not isinstance(stop_reason, dict):
        raise ValueError("runtime stop reason is invalid")
    code = stop_reason.get("code")
    if code not in _AGENT_STOP_CODES:
        raise ValueError("runtime stop code is invalid")
    step = stop_reason.get("step")
    detail = stop_reason.get("detail", "")
    snapshot = stop_reason.get("budget_snapshot")
    if (
        not isinstance(step, int)
        or isinstance(step, bool)
        or step < 0
        or step > 64
        or not isinstance(detail, str)
    ):
        raise ValueError("runtime stop reason fields are invalid")
    if not isinstance(snapshot, dict):
        raise ValueError("runtime budget snapshot is invalid")
    snapshot_fields = (
        "steps",
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "cost_microusd",
        "elapsed_ms",
    )
    if any(
        not isinstance(snapshot.get(field), int)
        or isinstance(snapshot[field], bool)
        or snapshot[field] < 0
        for field in snapshot_fields
    ):
        raise ValueError("runtime budget fields are invalid")
    usage = result.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("runtime usage is invalid")
    usage_fields = ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cost_microusd")
    if any(
        not isinstance(usage.get(field), int) or isinstance(usage[field], bool) or usage[field] < 0
        for field in usage_fields
    ):
        raise ValueError("runtime usage fields are invalid")
    elapsed_ms = result.get("elapsed_ms")
    if not isinstance(elapsed_ms, int) or isinstance(elapsed_ms, bool) or elapsed_ms < 0:
        raise ValueError("runtime elapsed time is invalid")
    final_answer = result.get("final_answer")
    safe_final_answer: dict[str, object] | None = None
    if code == "FINAL_ANSWER":
        if not isinstance(final_answer, dict):
            raise ValueError("runtime final answer is invalid")
        if final_answer.get("schema_version") != "1":
            raise ValueError("runtime final answer version is invalid")
        if final_answer.get("status") not in {"SUCCEEDED", "NEEDS_INPUT", "FAILED"}:
            raise ValueError("runtime final answer status is invalid")
        if not isinstance(final_answer.get("summary"), str) or not final_answer["summary"].strip():
            raise ValueError("runtime final answer summary is invalid")
        refs = final_answer.get("evidence_refs")
        unknowns = final_answer.get("unknowns")
        if not isinstance(refs, list) or not isinstance(unknowns, list):
            raise ValueError("runtime final answer references are invalid")
        if any(not isinstance(ref, str) or not re.fullmatch(r"[0-9a-f]{64}", ref) for ref in refs):
            raise ValueError("runtime evidence reference is invalid")
        if any(not isinstance(value, str) for value in unknowns):
            raise ValueError("runtime final answer unknowns are invalid")
        if len(final_answer["summary"].encode("utf-8")) > 4_000:
            raise ValueError("runtime final answer summary is too long")
        if len(refs) > 32 or len(unknowns) > 32:
            raise ValueError("runtime final answer references are too many")
        if any(len(value.encode("utf-8")) > 4_000 for value in unknowns):
            raise ValueError("runtime final answer unknown is too long")
        safe_final_answer = {
            "schema_version": "1",
            "status": final_answer["status"],
            "summary": _safe_trace_text(final_answer["summary"], 4_000),
            "evidence_refs": list(refs),
            "unknowns": [_safe_trace_text(value, 4_000) for value in unknowns],
        }
    _validate_trace_semantics(events, stop_code=code, final_answer=safe_final_answer)
    return {
        "schema_version": "1",
        "provider": _safe_trace_text(body["provider"], 120),
        "model": _safe_trace_text(body["model"], 200),
        "prompt_schema_version": "1",
        "tool_schema_version": "1",
        "result": {
            "stop_reason": {
                "schema_version": "1",
                "code": code,
                "step": step,
                "detail": detail[:500],
                "budget_snapshot": {field: snapshot[field] for field in snapshot_fields},
            },
            "final_answer": safe_final_answer,
            "events": safe_events,
            "usage": {field: usage[field] for field in usage_fields},
            "elapsed_ms": elapsed_ms,
        },
    }


def _execute_agent_via_runtime(
    run: Any,
    capability: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Call the internal Runtime and return only a validated, redacted response."""
    payload: dict[str, object] = {
        "schema_version": "1",
        "run_id": run.name,
        "correlation_id": correlation_id,
        "goal": run.goal,
        "capability": capability,
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    runtime_token = os.environ.get(_RUNTIME_TOKEN_ENV, "").strip()
    if runtime_token:
        headers["X-Synora-Runtime-Token"] = runtime_token
    request: urllib.request.Request | None = None
    try:
        request = urllib.request.Request(
            _runtime_agent_url(),
            data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=_RUNTIME_TIMEOUT_SECONDS) as response:
            raw = response.read(_RUNTIME_RESPONSE_BYTES + 1)
        if len(raw) > _RUNTIME_RESPONSE_BYTES or capability.encode() in raw:
            raise ValueError("runtime response is unsafe")
        body = json.loads(
            raw,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_json_pairs,
        )
        return _validate_agent_runtime_response(body, run.name)
    except Exception:
        return _runtime_failure_response(run.name)
    finally:
        payload.clear()
        headers.clear()
        request = None
        runtime_token = ""


def _agent_trace_status(code: str) -> str:
    if code == "FINAL_ANSWER":
        return "SUCCEEDED"
    if code in _AGENT_FALLBACK_CODES:
        return "FALLBACK"
    return "FAILED"


def _persist_agent_trace(
    run: Any,
    response: dict[str, Any],
    correlation_id: str,
) -> dict[str, Any]:
    result = response["result"]
    stop_reason = result["stop_reason"]
    events = result["events"]
    usage = result["usage"]
    try:
        attempt = int(frappe.db.count("Synora Agent Trace Attempt", filters={"run": run.name})) + 1
    except Exception:
        attempt = 1
    status = _agent_trace_status(stop_reason["code"])
    stop_json = frappe.as_json(_safe_trace_value(stop_reason))
    events_json = frappe.as_json(_safe_trace_value(events))
    frappe.get_doc(
        {
            "doctype": "Synora Agent Trace Attempt",
            "run": run.name,
            "attempt": attempt,
            "mode": "AGENT",
            "provider": response["provider"],
            "model": response["model"],
            "prompt_schema_version": response["prompt_schema_version"],
            "tool_schema_version": response["tool_schema_version"],
            "events_json": events_json,
            "events_count": len(events),
            "stop_reason": stop_json,
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "reasoning_tokens": usage["reasoning_tokens"],
            "cost_microusd": usage["cost_microusd"],
            "elapsed_ms": result["elapsed_ms"],
            "status": status,
            "correlation_id": correlation_id,
        }
    ).insert(ignore_permissions=True)
    return {"status": status, "code": stop_reason["code"], "attempt": attempt}


def _load_active_run(run_id: str, expected_states: frozenset[str]) -> Any:
    """统一入口校验: 存在性 + 归属 + capability 有效(ACTIVE/未撤销/未过期) + 业务状态。

    所有业务入口 (analyze/plan) 必须经过本校验, 防止已撤销/已过期的 Run
    绕过 capability 继续进入中间状态或读取工具调用。
    """
    if not frappe.db.exists("Synora Agent Run", run_id):
        raise GatewayFault("RUN_REJECTED", "run is not available", 404)
    run = frappe.get_doc("Synora Agent Run", run_id)
    actor = frappe.session.user
    if actor != run.initiator and "System Manager" not in frappe.get_roles(actor):
        raise GatewayFault("PERMISSION_DENIED", "run is not available", 403)
    if run.status != "ACTIVE" or run.revoked:
        raise GatewayFault("CONFLICT", "run is not active", 409)
    if get_datetime(run.expires_at) <= now_datetime():
        raise GatewayFault("CONFLICT", "run capability has expired", 409)
    if run.run_state not in expected_states:
        raise GatewayFault("CONFLICT", "run is not in required state", 409)
    return run


def _run_context(run: Any) -> RunContext:
    return RunContext(
        run_id=run.name,
        initiator=run.initiator,
        company=run.company_scope,
        warehouse=run.warehouse_scope or None,
        state_version=run.state_version,
    )


def _set_run_state(run: Any, target: str) -> None:
    """受控状态推进 (CAS)。

    双保险: ① save 前显式校验 state_version 未被并发修改; ② 依赖 Frappe 原生
    乐观锁 (save 时 modified 微秒对比, check_if_latest)。任一层检测到并发修改
    (并发分析/取消), 抛 GatewayFault CONFLICT —— 并发互斥、取消竞态防护、
    失败后旧请求不得复活。
    """
    validate_transition(run.run_state, target)
    run.flags.synora_state_change = True
    run.run_state = target
    run.state_version += 1
    expected = run.state_version - 1
    try:
        current_version = frappe.db.get_value(
            "Synora Agent Run", run.name, "state_version", ignore_permissions=True
        )
    except Exception:
        current_version = expected  # 读取失败: 交给乐观锁兜底, 不阻塞。
    if current_version != expected:
        raise GatewayFault("CONFLICT", "run state changed concurrently", 409)
    try:
        run.save(ignore_permissions=True)
    except frappe.TimestampMismatchError as exc:
        raise GatewayFault("CONFLICT", "run state changed concurrently", 409) from exc


def _recover_failed_analysis(run_id: str, correlation_id: str) -> None:
    """分析中途失败: 清理本次部分分析记录, 并把仍处于 ANALYZING 的 Run 回退 CREATED。

    回退只发生在"当前数据库仍为 ANALYZING"时 (重新读取); 若期间被取消或推进,
    则不再改动 (不复活、不覆盖并发结果)。清理按 correlation_id 限定本次请求
    已写入的不可变快照, 不影响历史分析记录。
    """
    try:
        frappe.db.delete("Synora Item Analysis", {"run": run_id, "correlation_id": correlation_id})
    except Exception:
        # 清理失败不应掩盖原始分析错误; 残留记录会在重试时按 run 聚合展示。
        pass
    try:
        current = frappe.get_doc("Synora Agent Run", run_id)
        if current.run_state != "ANALYZING":
            return
        _set_run_state(current, "CREATED")
    except GatewayFault:
        # 并发取消/推进已生效: 回退让位, 不覆盖。
        pass
    except frappe.TimestampMismatchError:
        # 并发取消/推进已生效: 回退让位, 不覆盖。
        pass


def _lock_proposed_run(run_id: str) -> Any:
    """锁住本 Run 的提议行, 避免并发 plan_run 重复调用模型。

    这是 Synora 自有 DocType 的事务行锁; 锁在本次请求结束时释放, 进程崩溃
    不会留下业务锁。模型调用仍有 20 秒上限, 锁的范围限定为单个 Run。
    """
    locked = frappe.db.sql(
        """
        SELECT name
        FROM `tabSynora Agent Run`
        WHERE name = %s
        FOR UPDATE
        """,
        (run_id,),
        as_dict=True,
    )
    if not locked:
        raise GatewayFault("RUN_REJECTED", "run is not available", 404)
    return _load_active_run(run_id, frozenset({"PROPOSED"}))


def _call_tool(
    ctx: RunContext, name: str, tool_input: dict[str, object], correlation_id: str
) -> dict[str, Any]:
    # Deterministic closeout runs in the Frappe process rather than through the
    # capability-authenticated Runtime HTTP path. Re-read the Run immediately
    # before every ERP call so a concurrent cancel cannot leave this stale
    # context executing another read after the Run became CANCELLED.
    if not frappe.db.exists("Synora Agent Run", ctx.run_id):
        raise GatewayFault("RUN_REJECTED", "run is not available", 404)
    current = frappe.get_doc("Synora Agent Run", ctx.run_id)
    if (
        current.status != "ACTIVE"
        or current.revoked
        or current.run_state != "ANALYZING"
        or current.state_version != ctx.state_version
        or get_datetime(current.expires_at) <= now_datetime()
    ):
        raise GatewayFault("CONFLICT", "run is no longer active for analysis", 409)
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


def _analyze_deterministic_run(
    run: Any,
    correlation_id: str,
    *,
    already_analyzing: bool = False,
) -> dict[str, Any]:
    """Run the Phase 3 analysis, optionally continuing an Agent exploration."""
    analysis_started = already_analyzing
    try:
        if not already_analyzing:
            _set_run_state(run, "ANALYZING")
            analysis_started = True
        # Capture the version after the CREATED -> ANALYZING transition. The
        # per-tool cancellation check compares this snapshot with the current
        # database row to reject stale closeout contexts.
        ctx = _run_context(run)

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
            _persist_analysis(run.name, item_analysis, correlation_id)
            analyses.append(item_analysis.to_dict())

        _set_run_state(run, "PROPOSED")
    except GatewayFault:
        # 工具失败/结果超限/并发冲突: 回退可重试, 不留永久中间态。
        if analysis_started:
            _recover_failed_analysis(run.name, correlation_id)
        raise
    except Exception:
        if analysis_started:
            _recover_failed_analysis(run.name, correlation_id)
        raise
    return {
        "run_id": run.name,
        "run_state": run.run_state,
        "state_version": run.state_version,
        "items_analyzed": len(analyses),
        "analyses": analyses,
    }


def analyze_run(run_id: str, correlation_id: str) -> dict[str, Any]:
    """Analyze a Run; Agent mode explores first, then deterministic code closes it."""
    run = _load_active_run(run_id, frozenset({"CREATED"}))
    if getattr(run, "execution_mode", "DETERMINISTIC") == "AGENT":
        return _analyze_agent_run(run, correlation_id)
    return _analyze_deterministic_run(run, correlation_id)


def _analyze_agent_run(run: Any, correlation_id: str) -> dict[str, Any]:
    analysis_started = False
    capability = ""
    try:
        _set_run_state(run, "ANALYZING")
        analysis_started = True
        from synora_agentic_erp.gateway.security import rotate_run_capability

        capability = rotate_run_capability(run)
        # Runtime -> Gateway is a separate HTTP request/DB transaction. Publish
        # ANALYZING plus the fresh digest before handing the short-lived
        # capability across that process boundary; otherwise Gateway can still
        # observe the pre-rotation CREATED snapshot.
        frappe.db.commit()
        try:
            response = _execute_agent_via_runtime(run, capability, correlation_id)
        finally:
            capability = ""
        trace = _persist_agent_trace(run, response, correlation_id)
        # Trace evidence must survive a later deterministic-analysis failure or
        # recovery rollback. It is an audit fact, not part of the analysis
        # result transaction.
        frappe.db.commit()
        code = str(trace["code"])
        if code in _AGENT_FALLBACK_CODES or code == "FINAL_ANSWER":
            current = _load_active_run(run.name, frozenset({"ANALYZING"}))
            return _analyze_deterministic_run(
                current,
                correlation_id,
                already_analyzing=True,
            )
        if code == "CANCELLED":
            raise GatewayFault("CONFLICT", "run was cancelled", 409)
        raise GatewayFault("ERP_ERROR", "Agent tool execution failed", 502)
    except GatewayFault:
        if analysis_started:
            _recover_failed_analysis(run.name, correlation_id)
        raise
    except Exception:
        if analysis_started:
            _recover_failed_analysis(run.name, correlation_id)
        raise
    finally:
        capability = ""


def plan_run(run_id: str, correlation_id: str) -> dict[str, Any]:
    """生成可解释只读计划 (PROPOSED -> SUCCEEDED, 只读无写入)。

    计划由确定性规则基于分析结果生成, 数量/金额/阈值不经过模型;
    每项结论带来源引用与未知说明。
    """
    # 在模型调用前串行化同一 Run; 唯一约束只能阻止重复落库, 不能阻止重复计费。
    run = _lock_proposed_run(run_id)

    analysis_docs = frappe.get_all(
        "Synora Item Analysis",
        filters={"run": run_id},
        fields=[
            "item_code",
            "risk",
            "actual_qty",
            "demand_qty",
            "incoming_qty",
            "open_mr_qty",
            "net_position",
            "shortage_qty",
            "unknowns",
        ],
        order_by="item_code asc",
        # run 归属已在上面按发起人校验, 子记录读取统一不看角色权限。
        ignore_permissions=True,
    )
    rows = tuple(
        AnalysisRow(
            item_code=doc.item_code,
            risk=doc.risk,
            actual_qty=str(doc.actual_qty),
            demand_qty=str(doc.demand_qty),
            incoming_qty=str(doc.incoming_qty),
            open_mr_qty=str(doc.open_mr_qty),
            net_position=str(doc.net_position),
            shortage_qty=str(doc.shortage_qty),
            unknowns=doc.unknowns or "",
        )
        for doc in analysis_docs
    )
    plan = build_plan(
        goal=run.goal,
        horizon_days=run.time_window_days,
        company=run.company_scope,
        warehouse=run.warehouse_scope or None,
        analyses=rows,
    )
    # 模型增强 (可选项): 数量/风险分类仍由 build_plan 确定性生成; 模型只改写
    # 解释文本, 严格校验失败或 Runtime/Provider 不可用 -> 回退确定性摘要,
    # 证据 (provider/token/耗时/回退原因) 一并持久化。
    plan_data = plan.to_dict()
    enhanced_text, evidence = _enhance_plan_via_runtime(plan_data)
    try:
        frappe.get_doc(
            {
                "doctype": "Synora Run Plan",
                "run": run_id,
                "goal": run.goal,
                "summary": plan.summary,
                "plan_json": frappe.as_json(plan_data),
                "enhanced_text": enhanced_text,
                "provider": evidence.get("provider"),
                "prompt_tokens": evidence.get("prompt_tokens", 0),
                "completion_tokens": evidence.get("completion_tokens", 0),
                "reasoning_tokens": evidence.get("reasoning_tokens", 0),
                "elapsed_ms": evidence.get("elapsed_ms", 0),
                "fallback_reason": evidence.get("fallback_reason"),
                "correlation_id": correlation_id,
            }
        ).insert(ignore_permissions=True)
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError) as exc:
        # run 字段唯一: 并发 plan_run 重复插入被 DB 层幂等拦截。
        raise GatewayFault("CONFLICT", "plan already generated", 409) from exc

    # SUCCEEDED 是只读终态: 同步撤销 capability, 防止 TTL 内继续调用只读工具。
    run.flags.synora_revocation = True
    run.revoked = 1
    run.status = "REVOKED"
    run.revoked_at = frappe.utils.now_datetime()
    run.revoked_by = frappe.session.user
    try:
        _set_run_state(run, "SUCCEEDED")
    except GatewayFault:
        # 推进失败 (并发冲突): 补偿删除本次插入的计划, 保持 PROPOSED 可重试,
        # 避免"已有计划但未 SUCCEEDED"导致 plan_run 永远 409 死锁。
        try:
            frappe.db.delete("Synora Run Plan", {"run": run_id, "correlation_id": correlation_id})
        except Exception:
            pass
        raise
    plan_result = plan.to_dict()
    plan_result["enhanced_text"] = enhanced_text
    plan_result["evidence"] = evidence
    return {
        "run_id": run_id,
        "run_state": run.run_state,
        "state_version": run.state_version,
        "plan": plan_result,
    }
