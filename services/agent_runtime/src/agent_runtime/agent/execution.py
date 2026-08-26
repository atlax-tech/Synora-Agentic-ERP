"""Internal Agent execution boundary for one bounded, read-only Run."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import ConfigDict, Field, SecretStr, TypeAdapter, field_validator

from agent_runtime.agent.budget import PricingConfigurationError, pricing_from_environment
from agent_runtime.agent.contracts import (
    Action,
    BudgetSnapshot,
    Observation,
    RunResult,
    StopReason,
    StrictModel,
    TraceRecorder,
    canonical_json,
    observation_from_summary,
)
from agent_runtime.agent.kernel import ToolAdapter, ToolExecutionFailure
from agent_runtime.agent.native_tool_calling import READ_TOOL_NAMES, run_native_tool_calling
from agent_runtime.gateway import (
    GatewayClient,
    GatewayClientError,
    GatewayRejected,
    GatewayRequest,
    GatewaySuccess,
    GatewayTimeoutError,
    ToolCall,
)
from agent_runtime.providers import (
    PROVIDER_MODEL_ENV,
    Provider,
    provider_from_environment,
)

SCHEMA_VERSION: Literal["1"] = "1"
PROMPT_SCHEMA_VERSION: Literal["1"] = "1"
TOOL_SCHEMA_VERSION: Literal["1"] = "1"
_TOOL_CALL_ADAPTER: TypeAdapter[ToolCall] = TypeAdapter(ToolCall)
_SENSITIVE_KEYS = {"password", "secret", "token", "capability", "authorization", "cookie"}


class GatewayClientLike(Protocol):
    async def execute(self, request: GatewayRequest) -> GatewaySuccess: ...

    async def aclose(self) -> None: ...


class AgentExecuteRequest(StrictModel):
    """Frappe-to-Runtime request; capability is never represented or logged."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
    )
    schema_version: Literal["1"] = SCHEMA_VERSION
    run_id: Annotated[UUID, Field(strict=False)]
    correlation_id: Annotated[UUID, Field(strict=False)]
    goal: str = Field(min_length=1, max_length=1_000)
    capability: SecretStr = Field(repr=False)

    @field_validator("capability")
    @classmethod
    def validate_capability(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if len(token) != 43 or any(
            not (character.isascii() and (character.isalnum() or character in "_-"))
            for character in token
        ):
            raise ValueError("capability is invalid")
        return value


class AgentExecuteResponse(StrictModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    provider: str = Field(min_length=1, max_length=120)
    model: str = Field(default="", max_length=200)
    prompt_schema_version: Literal["1"] = PROMPT_SCHEMA_VERSION
    tool_schema_version: Literal["1"] = TOOL_SCHEMA_VERSION
    result: RunResult


def _failure_result(
    request: AgentExecuteRequest,
    *,
    code: Literal["COST_BUDGET", "MODEL_ERROR"],
    detail: str,
) -> RunResult:
    recorder = TraceRecorder(request.run_id)
    recorder.add(
        "run.started",
        {"execution_mode": "AGENT", "tool_calling": "native", "tool_count": len(READ_TOOL_NAMES)},
    )
    recorder.add("run.stopped", {"code": code, "step": 0, "detail": detail})
    reason = StopReason(
        code=code,
        step=0,
        detail=detail,
        budget_snapshot=BudgetSnapshot(),
    )
    return RunResult(
        execution_mode="AGENT",
        final_answer=None,
        stop_reason=reason,
        events=recorder.events(),
        elapsed_ms=0,
    )


def _safe_row(row: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in list(row.items())[:20]:
        normalized_key = key.lower().replace("-", "_")
        if any(marker in normalized_key for marker in _SENSITIVE_KEYS):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = str(value)[:200] if isinstance(value, str) else value
    return safe


def _bounded_summary(envelope: GatewaySuccess) -> str:
    """Keep only a small, scalar observation; never forward the full ERP payload."""
    payload: dict[str, object] = {
        "tool": envelope.tool.name,
        "page": envelope.page.model_dump(mode="json"),
        "scope": {"company": envelope.authorized_scope.company},
        "data": [_safe_row(row) for row in envelope.data[:10]],
        "complete": envelope.completeness.status == "COMPLETE",
    }
    summary = canonical_json(payload)
    if len(summary) <= 3_800:
        return summary
    return canonical_json(
        {
            "tool": envelope.tool.name,
            "page": envelope.page.model_dump(mode="json"),
            "complete": envelope.completeness.status == "COMPLETE",
            "data_count": len(envelope.data),
            "truncated": True,
        }
    )


class GatewayToolAdapter(ToolAdapter):
    """Turn one validated Action into one capability-authenticated Gateway call."""

    def __init__(
        self,
        *,
        client: GatewayClientLike,
        run_id: UUID,
        correlation_id: UUID,
        capability: SecretStr,
    ) -> None:
        self._client: GatewayClientLike | None = client
        self._run_id = run_id
        self._correlation_id = correlation_id
        self._capability: SecretStr | None = capability

    async def execute(self, action: Action) -> Observation:
        if self._client is None or self._capability is None:
            raise ToolExecutionFailure("RUN_REJECTED", retryable=False)
        try:
            tool = _TOOL_CALL_ADAPTER.validate_python(
                {"name": action.tool_name, "version": "1", "input": action.canonical_args}
            )
            response = await self._client.execute(
                GatewayRequest(
                    run_id=self._run_id,
                    capability=self._capability,
                    correlation_id=self._correlation_id,
                    tool=tool,
                )
            )
        except GatewayRejected as error:
            raise ToolExecutionFailure(error.code, retryable=error.retryable) from None
        except GatewayTimeoutError:
            raise ToolExecutionFailure("TIMEOUT", retryable=True) from None
        except GatewayClientError:
            raise ToolExecutionFailure("GATEWAY_ERROR", retryable=True) from None
        except Exception:
            raise ToolExecutionFailure("GATEWAY_ERROR", retryable=False) from None
        return observation_from_summary(
            run_id=self._run_id,
            step=action.step,
            tool_name=action.tool_name,
            ok=True,
            summary=_bounded_summary(response),
        )

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        self._capability = None
        if client is not None:
            await client.aclose()


async def execute_agent(request: AgentExecuteRequest) -> AgentExecuteResponse:
    """Run native Tool Calling and return a redacted, deterministic contract."""
    provider_name = "byok-runtime"
    model_name = os.environ.get(PROVIDER_MODEL_ENV, "").strip()
    try:
        pricing = pricing_from_environment()
    except PricingConfigurationError:
        result = _failure_result(
            request,
            code="COST_BUDGET",
            detail="pricing configuration is incomplete or invalid",
        )
        return AgentExecuteResponse(provider=provider_name, model=model_name, result=result)
    if pricing is None:
        result = _failure_result(
            request,
            code="COST_BUDGET",
            detail="pricing is required before a paid Agent call",
        )
        return AgentExecuteResponse(provider=provider_name, model=model_name, result=result)
    provider: Provider | None = None
    client: GatewayClient | None = None
    try:
        # Acquire the Gateway client first. If provider construction then fails,
        # the already-owned client is closed before returning the safe failure.
        client = GatewayClient()
        provider = provider_from_environment()
    except Exception:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
        result = _failure_result(
            request,
            code="MODEL_ERROR",
            detail="provider or Gateway configuration is unavailable",
        )
        return AgentExecuteResponse(provider=provider_name, model=model_name, result=result)
    assert provider is not None and client is not None

    adapter = GatewayToolAdapter(
        client=client,
        run_id=request.run_id,
        correlation_id=request.correlation_id,
        capability=request.capability,
    )
    try:
        result = await run_native_tool_calling(
            run_id=request.run_id,
            correlation_id=request.correlation_id,
            goal=request.goal,
            provider=provider,
            tool_adapter=adapter,
            allowed_tools=frozenset(READ_TOOL_NAMES),
            pricing=pricing,
            require_pricing=True,
        )
        return AgentExecuteResponse(provider=provider_name, model=model_name, result=result)
    finally:
        # ``run_native_tool_calling`` closes both resources; this also clears the
        # adapter's in-memory capability reference before the request returns.
        await adapter.aclose()
