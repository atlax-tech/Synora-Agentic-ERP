import asyncio
import contextlib
import hashlib
import hmac
import os
import re
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_runtime.agent.enhance import (
    EnhancementEvidence,
    enhance_plan,
)
from agent_runtime.agent.execution import (
    AgentExecuteRequest,
    AgentExecuteResponse,
    execute_agent,
)
from agent_runtime.coach.contracts import CoachAnswer
from agent_runtime.coach.runtime import (
    CoachRuntimeRequest,
    answer_coach_runtime,
)
from agent_runtime.multi_agent.contracts import (
    MultiAgentResult,
    OrchestrationScope,
    plan_view_from_mapping,
)
from agent_runtime.multi_agent.planner_reviewer import run_planner_reviewer
from agent_runtime.providers import (
    Provider,
    ProviderError,
    provider_for_role,
    provider_from_environment,
    provider_model,
)
from agent_runtime.workflow.checkpoint import (
    CheckpointConflict,
    CheckpointError,
    CheckpointIncompatible,
    CheckpointUnavailable,
)
from agent_runtime.workflow.runtime import (
    WorkflowCancelRequest,
    WorkflowResponse,
    WorkflowResumeRequest,
    WorkflowRuntime,
    WorkflowStartRequest,
    WorkflowStatusRequest,
)

_RUNTIME_TOKEN_ENV = "SYNORA_RUNTIME_TOKEN"
_RUNTIME_TOKEN_HEADER = "X-Synora-Runtime-Token"
_SAFE_PROVIDER_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")


class HealthResponse(BaseModel):
    service: str
    status: str


class EnhanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    plan: dict[str, Any]
    provider_name: str = Field(
        default="byok-runtime",
        min_length=1,
        max_length=80,
    )
    orchestration_mode: Literal["single_agent", "planner_reviewer"] = "single_agent"
    orchestration_scope: OrchestrationScope | None = None

    @model_validator(mode="after")
    def require_orchestration_scope(self) -> EnhanceRequest:
        if self.orchestration_mode == "planner_reviewer" and self.orchestration_scope is None:
            raise ValueError("planner_reviewer requires an orchestration_scope")
        return self


class EnhanceResponse(BaseModel):
    explanation: str
    evidence: dict[str, Any]


def _require_runtime_token(http_request: Request) -> None:
    expected_token = os.environ.get(_RUNTIME_TOKEN_ENV, "").strip()
    if not expected_token:
        raise HTTPException(status_code=503, detail="runtime authentication is unavailable")
    if not hmac.compare_digest(http_request.headers.get(_RUNTIME_TOKEN_HEADER, ""), expected_token):
        raise HTTPException(status_code=401, detail="runtime authentication required")


def _workflow_error(error: Exception) -> HTTPException:
    if isinstance(error, CheckpointUnavailable):
        return HTTPException(status_code=503, detail="workflow checkpoint storage is unavailable")
    if isinstance(error, CheckpointIncompatible):
        return HTTPException(status_code=409, detail="workflow checkpoint is incompatible")
    if isinstance(error, (CheckpointConflict, CheckpointError)):
        return HTTPException(status_code=409, detail="workflow state conflict")
    return HTTPException(status_code=409, detail="workflow request was rejected")


def _safe_provider_label(value: str) -> str:
    """Keep user/provider labels out of evidence when they contain secrets."""
    return value if _SAFE_PROVIDER_LABEL.fullmatch(value) else "untrusted-provider"


app = FastAPI(
    title="Synora Agent Runtime",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.on_event("startup")
async def recover_persisted_workflows() -> None:
    """Convert orphaned in-flight tool steps into explicit manual recovery."""
    try:
        await WorkflowRuntime().recover()
    except CheckpointUnavailable:
        # PLAN_EXECUTE requests fail closed when storage is not configured; a
        # Runtime health check must remain available for deterministic/AGENT use.
        return


async def _execute_with_disconnect_guard(
    request: AgentExecuteRequest,
    http_request: Request,
    *,
    environ: dict[str, str] | None = None,
) -> AgentExecuteResponse:
    """Cancel the bounded kernel promptly when Frappe's request disappears."""
    values = dict(os.environ) if environ is None else environ
    task = asyncio.create_task(execute_agent(request, environ=values))
    try:
        while not task.done():
            if await http_request.is_disconnected():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                raise HTTPException(status_code=499, detail="request disconnected")
            await asyncio.sleep(0.05)
        return await task
    except BaseException:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        raise


async def _coach_with_disconnect_guard(
    request: CoachRuntimeRequest,
    http_request: Request,
    *,
    environ: dict[str, str] | None = None,
) -> CoachAnswer:
    """Cancel the Coach transport promptly when its internal caller disappears."""
    values = dict(os.environ) if environ is None else environ
    task = asyncio.create_task(answer_coach_runtime(request, environ=values))
    try:
        while not task.done():
            if await http_request.is_disconnected():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                raise HTTPException(status_code=499, detail="request disconnected")
            await asyncio.sleep(0.05)
        return await task
    except BaseException:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        raise


def _orchestration_summary(result: MultiAgentResult) -> dict[str, object]:
    """Expose only bounded counts, fixed stop code and digest-level trace."""
    return {
        "mode": "planner_reviewer",
        "model_calls": result.stop_reason.model_calls,
        "handoff_count": result.handoff_count,
        "revision_count": result.revision_count,
        "stop_reason": result.stop_reason.code,
        "deterministic_validated": result.deterministic_validated,
        "role_usage": [
            {
                "role_id": usage.role_id,
                "calls": usage.calls,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
                "elapsed_ms": usage.elapsed_ms,
            }
            for usage in result.role_usage
        ],
        "trace": {
            "event_count": result.trace.event_count,
            "event_types": list(result.trace.event_types),
            "digest": result.trace.digest,
        },
    }


def _missing_provider_orchestration_summary() -> dict[str, object]:
    return {
        "mode": "planner_reviewer",
        "model_calls": 0,
        "handoff_count": 0,
        "revision_count": 0,
        "stop_reason": "MODEL_ERROR",
        "deterministic_validated": False,
        "role_usage": [],
        "trace": {
            "event_count": 0,
            "event_types": [],
            "digest": hashlib.sha256(b"").hexdigest(),
        },
    }


_SAFE_ORCHESTRATION_FALLBACK = "无法生成计划解释，请人工核对确定性计划。"


def _safe_orchestration_fallback(
    plan: dict[str, Any], scope: OrchestrationScope | None = None
) -> str:
    try:
        view = plan_view_from_mapping(plan)
    except ValueError:
        return _SAFE_ORCHESTRATION_FALLBACK
    if scope is not None and (view.company != scope.company or view.warehouse != scope.warehouse):
        return _SAFE_ORCHESTRATION_FALLBACK
    return view.summary


def _provider_fallback_response(request: EnhanceRequest, provider_label: str) -> EnhanceResponse:
    evidence = EnhancementEvidence(
        provider=provider_label,
        prompt_tokens=0,
        completion_tokens=0,
        reasoning_tokens=0,
        elapsed_ms=0,
        status="fallback_error",
        fallback_reason="provider not configured",
    ).__dict__
    if request.orchestration_mode == "planner_reviewer":
        evidence["orchestration"] = _missing_provider_orchestration_summary()
    return EnhanceResponse(
        explanation=(
            _safe_orchestration_fallback(request.plan, request.orchestration_scope)
            if request.orchestration_mode == "planner_reviewer"
            else str(request.plan.get("summary", ""))
        ),
        evidence=evidence,
    )


@app.get("/healthz", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(service="synora-agent-runtime", status="ok")


@app.post("/enhance", response_model=EnhanceResponse)
async def enhance(request: EnhanceRequest, http_request: Request) -> EnhanceResponse:
    """把确定性计划增强为模型自然语言解释 (只读, 不产生业务写入)。

    数量/金额/阈值/风险分类全部由调用方 (Frappe plan_run) 确定性生成;
    本端点只让模型改写解释文本, 输出经严格校验, 失败回退确定性摘要。
    provider 未配置/调用失败 -> enhance_plan 内部回退, 本端点不抛 5xx。
    """
    environ = dict(os.environ)
    expected_token = environ.get(_RUNTIME_TOKEN_ENV, "").strip()
    if expected_token and not hmac.compare_digest(
        http_request.headers.get(_RUNTIME_TOKEN_HEADER, ""), expected_token
    ):
        raise HTTPException(status_code=401, detail="runtime authentication required")
    provider_label = _safe_provider_label(provider_model(environ) or request.provider_name)
    try:
        provider: Provider
        if request.orchestration_mode == "planner_reviewer":
            provider = provider_for_role("primary", environ=environ)
        else:
            provider = provider_from_environment(environ=environ)
    except ProviderError:
        # 未配置 BYOK: 回退确定性摘要并记录证据 (与 enhance_plan 回退语义一致)。
        return _provider_fallback_response(request, provider_label)
    except ValueError:
        return _provider_fallback_response(request, provider_label)
    if request.orchestration_mode == "planner_reviewer":
        try:
            orchestration_result = await run_planner_reviewer(
                request.plan,
                provider,
                provider_name=provider_label,
                scope=request.orchestration_scope,
            )
        finally:
            close = getattr(provider, "aclose", None)
            if close is not None:
                await close()
        usage = orchestration_result.role_usage
        orchestration_evidence = {
            "provider": provider_label,
            "prompt_tokens": sum(item.prompt_tokens for item in usage),
            "completion_tokens": sum(item.completion_tokens for item in usage),
            "reasoning_tokens": sum(item.reasoning_tokens for item in usage),
            "elapsed_ms": orchestration_result.stop_reason.elapsed_ms,
            "status": (
                "orchestration_ok"
                if orchestration_result.stop_reason.code in {"ACCEPTED", "REVISED_ACCEPTED"}
                else "orchestration_fallback"
            ),
            "fallback_reason": (
                None
                if orchestration_result.stop_reason.code in {"ACCEPTED", "REVISED_ACCEPTED"}
                else orchestration_result.stop_reason.detail
            ),
            "orchestration": _orchestration_summary(orchestration_result),
        }
        return EnhanceResponse(
            explanation=orchestration_result.final_text,
            evidence=orchestration_evidence,
        )
    try:
        explanation, enhancement_evidence = await enhance_plan(
            request.plan,
            provider,
            provider_name=provider_label,
            context_environ=environ,
        )
    finally:
        close = getattr(provider, "aclose", None)
        if close is not None:
            await close()
    return EnhanceResponse(explanation=explanation, evidence=enhancement_evidence.__dict__)


@app.post("/agent/execute", response_model=AgentExecuteResponse)
async def execute_agent_run(
    request: AgentExecuteRequest,
    http_request: Request,
) -> AgentExecuteResponse:
    """Internal Frappe-to-Runtime read-only Agent execution endpoint."""
    _require_runtime_token(http_request)
    return await _execute_with_disconnect_guard(request, http_request, environ=dict(os.environ))


@app.post("/coach/answer", response_model=CoachAnswer)
async def answer_coach_run(
    request: CoachRuntimeRequest,
    http_request: Request,
) -> CoachAnswer:
    """Internal Frappe-to-Runtime read-only contextual Coach endpoint."""
    _require_runtime_token(http_request)
    return await _coach_with_disconnect_guard(request, http_request, environ=dict(os.environ))


@app.post("/workflow/start", response_model=WorkflowResponse)
async def workflow_start(request: WorkflowStartRequest, http_request: Request) -> WorkflowResponse:
    """Start or idempotently continue one persisted read-only workflow."""
    _require_runtime_token(http_request)
    try:
        return await WorkflowRuntime().start(request)
    except Exception as error:
        raise _workflow_error(error) from error


@app.post("/workflow/resume", response_model=WorkflowResponse)
async def workflow_resume(
    request: WorkflowResumeRequest, http_request: Request
) -> WorkflowResponse:
    """Resume exactly one current clarification revision."""
    _require_runtime_token(http_request)
    try:
        return await WorkflowRuntime().resume(request)
    except Exception as error:
        raise _workflow_error(error) from error


@app.post("/workflow/cancel", response_model=WorkflowResponse)
async def workflow_cancel(
    request: WorkflowCancelRequest, http_request: Request
) -> WorkflowResponse:
    """Best-effort Runtime checkpoint cancellation after Frappe CAS."""
    _require_runtime_token(http_request)
    try:
        return WorkflowRuntime().cancel(request)
    except Exception as error:
        raise _workflow_error(error) from error


@app.post("/workflow/status", response_model=WorkflowResponse)
async def workflow_status(
    request: WorkflowStatusRequest, http_request: Request
) -> WorkflowResponse:
    """Return only persisted orchestration state; no ERP fact is synthesized."""
    _require_runtime_token(http_request)
    try:
        return WorkflowRuntime().status(request)
    except Exception as error:
        raise _workflow_error(error) from error
