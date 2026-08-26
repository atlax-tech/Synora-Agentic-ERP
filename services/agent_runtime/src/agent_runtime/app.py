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
from agent_runtime.providers import PROVIDER_MODEL_ENV, ProviderError, provider_from_environment

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


app = FastAPI(
    title="Synora Agent Runtime",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


async def _execute_with_disconnect_guard(
    request: AgentExecuteRequest, http_request: Request
) -> AgentExecuteResponse:
    """Cancel the bounded kernel promptly when Frappe's request disappears."""
    task = asyncio.create_task(execute_agent(request))
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
    expected_token = os.environ.get(_RUNTIME_TOKEN_ENV, "").strip()
    if expected_token and not hmac.compare_digest(
        http_request.headers.get(_RUNTIME_TOKEN_HEADER, ""), expected_token
    ):
        raise HTTPException(status_code=401, detail="runtime authentication required")
    provider_label = os.environ.get(PROVIDER_MODEL_ENV, "").strip() or request.provider_name
    try:
        provider = provider_from_environment()
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
            request.plan, provider, provider_name=provider_label
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
    expected_token = os.environ.get(_RUNTIME_TOKEN_ENV, "").strip()
    supplied_token = http_request.headers.get(_RUNTIME_TOKEN_HEADER, "")
    if not expected_token:
        raise HTTPException(status_code=503, detail="runtime authentication is unavailable")
    if not hmac.compare_digest(supplied_token, expected_token):
        raise HTTPException(status_code=401, detail="runtime authentication required")
    return await _execute_with_disconnect_guard(request, http_request)
