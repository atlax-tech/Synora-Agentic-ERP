import asyncio
import contextlib
import hmac
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

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
from agent_runtime.providers import ProviderError, provider_from_environment, provider_model
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


class HealthResponse(BaseModel):
    service: str
    status: str


class EnhanceRequest(BaseModel):
    plan: dict[str, Any]
    provider_name: str = "byok-runtime"


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
    provider_label = provider_model(environ) or request.provider_name
    try:
        provider = provider_from_environment(environ=environ)
    except (ProviderError, ValueError) as error:
        # 未配置 BYOK: 回退确定性摘要并记录证据 (与 enhance_plan 回退语义一致)。
        return EnhanceResponse(
            explanation=str(request.plan.get("summary", "")),
            evidence=EnhancementEvidence(
                provider=provider_label,
                prompt_tokens=0,
                completion_tokens=0,
                reasoning_tokens=0,
                elapsed_ms=0,
                status="fallback_error",
                fallback_reason=f"provider not configured: {error}",
            ).__dict__,
        )
    try:
        explanation, evidence = await enhance_plan(
            request.plan,
            provider,
            provider_name=provider_label,
            context_environ=environ,
        )
    finally:
        close = getattr(provider, "aclose", None)
        if close is not None:
            await close()
    return EnhanceResponse(explanation=explanation, evidence=evidence.__dict__)


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
