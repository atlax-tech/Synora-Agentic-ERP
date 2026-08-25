from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from agent_runtime.agent.enhance import (
    EnhancementEvidence,
    enhance_plan,
)
from agent_runtime.providers import ProviderError, provider_from_environment


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


@app.get("/healthz", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(service="synora-agent-runtime", status="ok")


@app.post("/enhance", response_model=EnhanceResponse)
async def enhance(request: EnhanceRequest) -> EnhanceResponse:
    """把确定性计划增强为模型自然语言解释 (只读, 不产生业务写入)。

    数量/金额/阈值/风险分类全部由调用方 (Frappe plan_run) 确定性生成;
    本端点只让模型改写解释文本, 输出经严格校验, 失败回退确定性摘要。
    provider 未配置/调用失败 -> enhance_plan 内部回退, 本端点不抛 5xx。
    """
    try:
        provider = provider_from_environment()
    except (ProviderError, ValueError) as error:
        # 未配置 BYOK: 回退确定性摘要并记录证据 (与 enhance_plan 回退语义一致)。
        return EnhanceResponse(
            explanation=str(request.plan.get("summary", "")),
            evidence=EnhancementEvidence(
                provider=request.provider_name,
                prompt_tokens=0,
                completion_tokens=0,
                elapsed_ms=0,
                status="fallback_error",
                fallback_reason=f"provider not configured: {error}",
            ).__dict__,
        )
    try:
        explanation, evidence = await enhance_plan(
            request.plan, provider, provider_name=request.provider_name
        )
    finally:
        close = getattr(provider, "aclose", None)
        if close is not None:
            await close()
    return EnhanceResponse(explanation=explanation, evidence=evidence.__dict__)
