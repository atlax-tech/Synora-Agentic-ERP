"""Provider-native function calling over the six read-only Gateway tools.

This module is an offline-friendly adapter: it accepts a typed provider and a
recorded or real Gateway adapter, but never receives ERP credentials or writes.
The provider's tool call is still untrusted until the local allowlist and the
existing Gateway ToolCall union both accept it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from time import monotonic
from typing import cast
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from agent_runtime.agent.contracts import (
    Action,
    BudgetSnapshot,
    FinalAnswer,
    JsonValue,
    Observation,
    RunResult,
    StopCode,
    StopReason,
    StrictModel,
    ToolName,
    TraceRecorder,
    UsageSnapshot,
    observation_from_summary,
    validate_action_tool,
)
from agent_runtime.agent.kernel import ToolAdapter, ToolExecutionFailure
from agent_runtime.gateway import (
    ItemLookupInput,
    OpenDemandInput,
    OpenMaterialRequestInput,
    OpenPurchaseOrderInput,
    ProjectedStockInput,
    SupplierLookupInput,
)
from agent_runtime.providers import (
    Provider,
    ProviderMessage,
    ProviderResponse,
    ProviderToolSpec,
)

READ_TOOL_NAMES: tuple[ToolName, ...] = (
    "item.lookup",
    "supplier.lookup",
    "stock.projected",
    "demand.open",
    "material_request.open",
    "purchase_order.open",
)


class NativeToolCallingLimits(StrictModel):
    """Small P4.3 limits; P4.4 owns the complete budget policy."""

    max_steps: int = Field(default=6, ge=1, le=64)
    max_output_tokens: int = Field(default=512, ge=1, le=512)


_TOOL_INPUTS: dict[ToolName, type[BaseModel]] = {
    "item.lookup": ItemLookupInput,
    "supplier.lookup": SupplierLookupInput,
    "stock.projected": ProjectedStockInput,
    "demand.open": OpenDemandInput,
    "material_request.open": OpenMaterialRequestInput,
    "purchase_order.open": OpenPurchaseOrderInput,
}


def provider_tool_specs(
    allowed_tools: frozenset[ToolName],
) -> tuple[ProviderToolSpec, ...]:
    """Translate the closed Gateway input schemas to provider function tools."""
    specs: list[ProviderToolSpec] = []
    for name in READ_TOOL_NAMES:
        if name not in allowed_tools:
            continue
        parameters = cast(dict[str, object], _TOOL_INPUTS[name].model_json_schema())
        specs.append(
            ProviderToolSpec(
                name=name,
                description="Read-only ERP observation; no business writes",
                parameters=parameters,
            )
        )
    return tuple(specs)


def build_tool_result_message(
    *,
    provider_tool_call_id: str,
    tool_name: ToolName,
    observation: Observation,
) -> ProviderMessage:
    """Create the bounded OpenAI-compatible ``tool`` result message."""
    return ProviderMessage(
        role="tool",
        tool_call_id=provider_tool_call_id,
        name=tool_name,
        content=observation.summary,
    )


class _SetRepeatGuard:
    def __init__(self) -> None:
        self._seen: set[str] = set()

    def check(self, action: Action) -> bool:
        key = action.call_key()
        if key in self._seen:
            return True
        self._seen.add(key)
        return False


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parse_arguments(raw: str) -> dict[str, JsonValue]:
    parsed = json.loads(
        raw,
        parse_constant=_reject_constant,
        object_pairs_hook=_unique_pairs,
    )
    if not isinstance(parsed, dict):
        raise ValueError("tool arguments must be a JSON object")
    return cast(dict[str, JsonValue], parsed)


def _parse_final_text(text: str) -> FinalAnswer:
    if not text.strip():
        raise ValueError("provider returned no final answer")
    raw = json.loads(
        text,
        parse_constant=_reject_constant,
        object_pairs_hook=_unique_pairs,
    )
    if not isinstance(raw, dict):
        raise ValueError("final answer must be a JSON object")
    values = dict(raw)
    values.pop("type", None)
    values.pop("schema_version", None)
    values.pop("stop_reason", None)
    for name in ("evidence_refs", "unknowns"):
        if isinstance(values.get(name), list):
            values[name] = tuple(values[name])
    return FinalAnswer.model_validate(values)


def _usage(response: ProviderResponse, current: UsageSnapshot) -> UsageSnapshot:
    return UsageSnapshot(
        prompt_tokens=current.prompt_tokens + response.prompt_tokens,
        completion_tokens=current.completion_tokens + response.completion_tokens,
        reasoning_tokens=current.reasoning_tokens + response.reasoning_tokens,
        cost_microusd=current.cost_microusd,
    )


def _stop(
    *,
    recorder: TraceRecorder,
    run_id: UUID,
    code: StopCode,
    step: int,
    detail: str,
    started: float,
    usage: UsageSnapshot,
    final_answer: FinalAnswer | None = None,
) -> RunResult:
    del run_id
    reason = StopReason(
        code=code,
        step=step,
        detail=detail,
        budget_snapshot=BudgetSnapshot(
            steps=step,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cost_microusd=usage.cost_microusd,
            elapsed_ms=max(0, int((monotonic() - started) * 1000)),
        ),
    )
    if final_answer is not None:
        final_answer = final_answer.model_copy(update={"stop_reason": reason})
    recorder.add("run.stopped", {"code": code, "step": step, "detail": detail})
    return RunResult(
        execution_mode="AGENT",
        final_answer=final_answer,
        stop_reason=reason,
        events=recorder.events(),
        usage=usage,
        elapsed_ms=reason.budget_snapshot.elapsed_ms,
    )


async def run_native_tool_calling(
    *,
    run_id: UUID,
    correlation_id: UUID,
    goal: str,
    provider: Provider,
    tool_adapter: ToolAdapter,
    allowed_tools: frozenset[ToolName],
    limits: NativeToolCallingLimits | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> RunResult:
    """Execute one-at-a-time native tool calls with typed local guards."""
    effective_limits = limits or NativeToolCallingLimits()
    started = monotonic()
    usage = UsageSnapshot()
    recorder = TraceRecorder(run_id)
    recorder.add(
        "run.started",
        {
            "execution_mode": "AGENT",
            "tool_calling": "native",
            "max_steps": effective_limits.max_steps,
        },
    )
    tools = provider_tool_specs(allowed_tools)
    messages: list[ProviderMessage] = [
        ProviderMessage(
            role="system",
            content="Use one read-only function call or return typed final JSON.",
        ),
        ProviderMessage(role="user", content=goal),
    ]
    observations: list[Observation] = []
    guard = _SetRepeatGuard()

    for step in range(1, effective_limits.max_steps + 1):
        if cancelled is not None and cancelled():
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="CANCELLED",
                step=step,
                detail="execution was cancelled",
                started=started,
                usage=usage,
            )
        recorder.add("model.requested", {"step": step, "tool_count": len(tools)})
        try:
            response = await provider.complete(
                messages,
                tools=list(tools),
                max_tokens=effective_limits.max_output_tokens,
            )
            usage = _usage(response, usage)
        except Exception:
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="MODEL_ERROR",
                step=step,
                detail="native provider failed",
                started=started,
                usage=usage,
            )

        if len(response.tool_calls) > 1:
            recorder.add("action.rejected", {"step": step, "reason": "parallel tool calls"})
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="MODEL_ERROR",
                step=step,
                detail="parallel tool calls are not supported",
                started=started,
                usage=usage,
            )
        if not response.tool_calls:
            try:
                final = _parse_final_text(response.text)
            except ValueError, TypeError, ValidationError:
                recorder.add("final.rejected", {"step": step, "reason": "invalid final JSON"})
                return _stop(
                    recorder=recorder,
                    run_id=run_id,
                    code="UNSUPPORTED_FINAL_ANSWER",
                    step=step,
                    detail="native provider final answer was not typed JSON",
                    started=started,
                    usage=usage,
                )
            recorder.add(
                "final.proposed",
                {"step": step, "evidence_refs": list(final.evidence_refs)},
            )
            known_digests = {observation.digest for observation in observations if observation.ok}
            if not final.evidence_refs or not set(final.evidence_refs).issubset(known_digests):
                recorder.add("final.rejected", {"step": step, "reason": "evidence ref is unknown"})
                return _stop(
                    recorder=recorder,
                    run_id=run_id,
                    code="UNSUPPORTED_FINAL_ANSWER",
                    step=step,
                    detail="final answer did not cite an observed digest",
                    started=started,
                    usage=usage,
                )
            recorder.add("final.validated", {"step": step})
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="FINAL_ANSWER",
                step=step,
                detail="validated native final answer",
                started=started,
                usage=usage,
                final_answer=final,
            )

        provider_call = response.tool_calls[0]
        recorder.add(
            "action.proposed",
            {"step": step, "tool_name": provider_call.name},
        )
        if provider_call.name not in allowed_tools:
            recorder.add("action.rejected", {"step": step, "reason": "tool is not allowed"})
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="TOOL_NOT_ALLOWED",
                step=step,
                detail="native tool is outside the current allowlist",
                started=started,
                usage=usage,
            )
        try:
            arguments = _parse_arguments(provider_call.arguments)
            action = Action(
                step=step,
                tool_name=provider_call.name,
                canonical_args=arguments,
                correlation_id=correlation_id,
            )
            validate_action_tool(action)
        except ValidationError, TypeError, ValueError:
            recorder.add("action.rejected", {"step": step, "reason": "invalid tool arguments"})
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="INVALID_TOOL_ARGS",
                step=step,
                detail="native tool arguments failed typed validation",
                started=started,
                usage=usage,
            )
        recorder.add("action.validated", {"step": step, "tool_name": action.tool_name})
        if guard.check(action):
            recorder.add(
                "guard.checked",
                {"step": step, "guard": "repeated_call", "allowed": False},
            )
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="REPEATED_CALL",
                step=step,
                detail="the same native tool call was already used",
                started=started,
                usage=usage,
            )
        recorder.add("guard.checked", {"step": step, "guard": "repeated_call", "allowed": True})
        recorder.add("tool.started", {"step": step, "tool_name": action.tool_name})
        try:
            observation = await tool_adapter.execute(action)
        except ToolExecutionFailure as error:
            observation = observation_from_summary(
                run_id=run_id,
                step=step,
                tool_name=action.tool_name,
                ok=False,
                summary="tool execution failed",
                error_code=error.code,
                retryable=error.retryable,
            )
            recorder.add(
                "tool.failed",
                {"step": step, "tool_name": action.tool_name, "error_code": error.code},
            )
            observations.append(observation)
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="TOOL_ERROR",
                step=step,
                detail="native tool adapter returned a classified failure",
                started=started,
                usage=usage,
            )
        except Exception:
            recorder.add("tool.failed", {"step": step, "tool_name": action.tool_name})
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="TOOL_ERROR",
                step=step,
                detail="native tool adapter failed",
                started=started,
                usage=usage,
            )
        if observation.tool_name != action.tool_name or observation.step != step:
            recorder.add(
                "tool.failed",
                {
                    "step": step,
                    "tool_name": action.tool_name,
                    "reason": "observation context mismatch",
                },
            )
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="TOOL_ERROR",
                step=step,
                detail="native observation did not match the action context",
                started=started,
                usage=usage,
            )
        observations.append(observation)
        recorder.add(
            "tool.observed",
            {
                "step": step,
                "tool_name": action.tool_name,
                "ok": observation.ok,
                "digest": observation.digest,
                "summary": observation.summary,
            },
        )
        messages.append(
            ProviderMessage(
                role="assistant",
                content="",
                tool_calls=(provider_call,),
            )
        )
        messages.append(
            build_tool_result_message(
                provider_tool_call_id=provider_call.id,
                tool_name=action.tool_name,
                observation=observation,
            )
        )

    return _stop(
        recorder=recorder,
        run_id=run_id,
        code="MAX_STEPS",
        step=effective_limits.max_steps,
        detail="maximum native tool-calling steps reached",
        started=started,
        usage=usage,
    )
