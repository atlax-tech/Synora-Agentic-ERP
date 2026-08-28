"""Provider-native function calling over the six read-only Gateway tools.

This module is an offline-friendly adapter: it accepts a typed provider and a
recorded or real Gateway adapter, but never receives ERP credentials or writes.
The provider's tool call is still untrusted until the local allowlist and the
existing Gateway ToolCall union both accept it.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Callable
from time import monotonic
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from agent_runtime.agent.budget import BudgetAccount, BudgetLimits, Pricing
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
from agent_runtime.agent.guards import NoProgressGuard, RepeatedCallGuard, ToolFrequencyGuard
from agent_runtime.agent.kernel import ToolAdapter, ToolExecutionFailure
from agent_runtime.agent.prompting import (
    NATIVE_AGENT_PROFILE_ID,
    PromptVariant,
    build_prompt_messages,
)
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
    ProviderError,
    ProviderMessage,
    ProviderResponse,
    ProviderToolSpec,
)

_NATIVE_PARSE_ERRORS = (ValueError, TypeError, ValidationError)

READ_TOOL_NAMES: tuple[ToolName, ...] = (
    "item.lookup",
    "supplier.lookup",
    "stock.projected",
    "demand.open",
    "material_request.open",
    "purchase_order.open",
)


class NativeToolCallingLimits(BudgetLimits):
    """Balanced P4.4 limits used by the native provider path."""


class _ProviderFinalAnswer(StrictModel):
    """Strict wire shape accepted from a provider's final text response."""

    # Both discriminator fields are required on the provider wire. Defaults
    # would turn a truncated or legacy response into an apparently valid answer.
    type: Literal["final"]
    schema_version: Literal["1"]
    status: Literal["SUCCEEDED", "NEEDS_INPUT", "FAILED"]
    summary: str = Field(min_length=1, max_length=4_000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=32)
    unknowns: list[str] = Field(default_factory=list, max_length=32)


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
    wire = _ProviderFinalAnswer.model_validate(raw)
    return FinalAnswer(
        status=wire.status,
        summary=wire.summary,
        evidence_refs=tuple(wire.evidence_refs),
        unknowns=tuple(wire.unknowns),
    )


_NUMBER_TOKEN = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")


def _has_unsupported_numeric_claim(
    final: FinalAnswer,
    observations: list[Observation],
) -> bool:
    """Reject numeric claims not present in the observations they cite."""
    cited = set(final.evidence_refs)
    evidence_text = "\n".join(
        observation.summary
        for observation in observations
        if observation.ok and observation.digest in cited
    )
    claimed_numbers = set(_NUMBER_TOKEN.findall(final.summary))
    observed_numbers = set(_NUMBER_TOKEN.findall(evidence_text))
    return not claimed_numbers.issubset(observed_numbers)


def _stop(
    *,
    recorder: TraceRecorder,
    run_id: UUID,
    code: StopCode,
    step: int,
    detail: str,
    started: float,
    usage: UsageSnapshot,
    elapsed_ms: int | None = None,
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
            elapsed_ms=(
                elapsed_ms
                if elapsed_ms is not None
                else max(0, int((monotonic() - started) * 1000))
            ),
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


async def _run_native_tool_calling(
    *,
    run_id: UUID,
    correlation_id: UUID,
    goal: str,
    provider: Provider,
    tool_adapter: ToolAdapter,
    allowed_tools: frozenset[ToolName],
    limits: NativeToolCallingLimits | None = None,
    cancelled: Callable[[], bool] | None = None,
    pricing: Pricing | None = None,
    require_pricing: bool = False,
    clock: Callable[[], float] = monotonic,
    prompt_variant: PromptVariant = "A",
) -> RunResult:
    """Execute one-at-a-time native tool calls with the P4.4 budget policy."""
    effective_limits = limits or NativeToolCallingLimits()
    started = clock()
    account = BudgetAccount(
        limits=effective_limits,
        pricing=pricing,
        require_pricing=require_pricing,
        started=started,
        clock=clock,
    )
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
    messages, _ = build_prompt_messages(
        NATIVE_AGENT_PROFILE_ID,
        variant=prompt_variant,
        user_content=goal,
    )
    observations: list[Observation] = []
    repeat_guard = RepeatedCallGuard()
    frequency_guard = ToolFrequencyGuard(max_calls_per_tool=effective_limits.max_calls_per_tool)
    no_progress_guard = NoProgressGuard(threshold=effective_limits.no_progress_threshold)

    for step in range(1, effective_limits.max_steps + 1):
        if cancelled is not None and cancelled():
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="CANCELLED",
                step=step,
                detail="execution was cancelled",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )
        budget_code = account.preflight(messages=messages, tools=tools)
        if budget_code is not None:
            recorder.add(
                "guard.checked",
                {"step": step, "guard": budget_code, "allowed": False},
            )
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code=budget_code,
                step=step,
                detail="budget preflight failed before provider call",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )
        remaining_seconds = max(
            0.0,
            (effective_limits.max_wall_time_ms - account.elapsed_ms()) / 1000,
        )
        if remaining_seconds <= 0:
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="WALL_TIME_BUDGET",
                step=step,
                detail="wall-clock budget expired before provider call",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )
        recorder.add("model.requested", {"step": step, "tool_count": len(tools)})
        try:
            response = await asyncio.wait_for(
                provider.complete(
                    messages,
                    tools=list(tools),
                    max_tokens=effective_limits.max_output_tokens,
                ),
                timeout=remaining_seconds,
            )
            budget_code = account.record(response)
        except ProviderError as error:
            # Provider-side budget failures can still carry usage. Account for
            # those numbers before returning the typed stop reason so Trace can
            # audit what was observed without exposing provider error text.
            usage_code = account.record(
                ProviderResponse(
                    prompt_tokens=error.prompt_tokens,
                    completion_tokens=error.completion_tokens,
                    reasoning_tokens=error.reasoning_tokens,
                )
            )
            provider_code = error.budget_code or usage_code
            if provider_code is not None:
                recorder.add(
                    "guard.checked",
                    {"step": step, "guard": provider_code, "allowed": False},
                )
                return _stop(
                    recorder=recorder,
                    run_id=run_id,
                    code=provider_code,
                    step=step,
                    detail="provider usage was unavailable or exceeded the bounded budget",
                    started=started,
                    usage=account.usage,
                    elapsed_ms=account.elapsed_ms(),
                )
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="MODEL_ERROR",
                step=step,
                detail="native provider failed",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )
        except TimeoutError:
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="WALL_TIME_BUDGET",
                step=step,
                detail="provider call exceeded the wall-clock budget",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )
        except asyncio.CancelledError:
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="CANCELLED",
                step=step,
                detail="provider call was cancelled",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )
        except Exception:
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="MODEL_ERROR",
                step=step,
                detail="native provider failed",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )

        if budget_code is not None:
            recorder.add(
                "guard.checked",
                {"step": step, "guard": budget_code, "allowed": False},
            )
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code=budget_code,
                step=step,
                detail="provider usage exceeded the bounded run budget",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )
        if cancelled is not None and cancelled():
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="CANCELLED",
                step=step,
                detail="execution was cancelled after provider response",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
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
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )
        if not response.tool_calls:
            try:
                final = _parse_final_text(response.text)
            except _NATIVE_PARSE_ERRORS:
                recorder.add("final.rejected", {"step": step, "reason": "invalid final JSON"})
                return _stop(
                    recorder=recorder,
                    run_id=run_id,
                    code="UNSUPPORTED_FINAL_ANSWER",
                    step=step,
                    detail="native provider final answer was not typed JSON",
                    started=started,
                    usage=account.usage,
                    elapsed_ms=account.elapsed_ms(),
                )
            recorder.add(
                "final.proposed",
                {
                    "step": step,
                    "status": final.status,
                    "summary": final.summary,
                    "evidence_refs": list(final.evidence_refs),
                    "unknowns": list(final.unknowns),
                },
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
                    usage=account.usage,
                    elapsed_ms=account.elapsed_ms(),
                )
            if _has_unsupported_numeric_claim(final, observations):
                recorder.add(
                    "final.rejected",
                    {"step": step, "reason": "unsupported numeric claim"},
                )
                return _stop(
                    recorder=recorder,
                    run_id=run_id,
                    code="UNSUPPORTED_FINAL_ANSWER",
                    step=step,
                    detail="final answer contained a number absent from cited observations",
                    started=started,
                    usage=account.usage,
                    elapsed_ms=account.elapsed_ms(),
                )
            recorder.add("final.validated", {"step": step})
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="FINAL_ANSWER",
                step=step,
                detail="validated native final answer",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
                final_answer=final,
            )

        provider_call = response.tool_calls[0]
        if provider_call.name not in allowed_tools:
            recorder.add("action.rejected", {"step": step, "reason": "tool is not allowed"})
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="TOOL_NOT_ALLOWED",
                step=step,
                detail="native tool is outside the current allowlist",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
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
        except _NATIVE_PARSE_ERRORS:
            recorder.add("action.rejected", {"step": step, "reason": "invalid tool arguments"})
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="INVALID_TOOL_ARGS",
                step=step,
                detail="native tool arguments failed typed validation",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )
        recorder.add(
            "action.proposed",
            {
                "step": step,
                "tool_name": action.tool_name,
                "canonical_args": action.canonical_args,
            },
        )
        recorder.add("action.validated", {"step": step, "tool_name": action.tool_name})
        if repeat_guard.check(action):
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
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )
        if frequency_guard.check(action):
            recorder.add(
                "guard.checked",
                {
                    "step": step,
                    "guard": "tool_frequency",
                    "allowed": False,
                    "count": frequency_guard.count(action.tool_name),
                },
            )
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="TOOL_FREQUENCY",
                step=step,
                detail="the same tool exceeded its per-run call frequency",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )
        recorder.add(
            "guard.checked",
            {
                "step": step,
                "guard": "tool_frequency",
                "allowed": True,
                "count": frequency_guard.count(action.tool_name),
            },
        )
        recorder.add("guard.checked", {"step": step, "guard": "repeated_call", "allowed": True})
        recorder.add(
            "tool.started",
            {
                "step": step,
                "tool_name": action.tool_name,
                "canonical_args": action.canonical_args,
            },
        )
        remaining_seconds = max(
            0.0,
            (effective_limits.max_wall_time_ms - account.elapsed_ms()) / 1000,
        )
        if remaining_seconds <= 0:
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="WALL_TIME_BUDGET",
                step=step,
                detail="wall-clock budget expired before tool call",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )
        try:
            observation = await asyncio.wait_for(
                tool_adapter.execute(action),
                timeout=remaining_seconds,
            )
        except TimeoutError:
            recorder.add("tool.failed", {"step": step, "tool_name": action.tool_name})
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="WALL_TIME_BUDGET",
                step=step,
                detail="tool call exceeded the wall-clock budget",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )
        except asyncio.CancelledError:
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="CANCELLED",
                step=step,
                detail="tool call was cancelled",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )
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
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
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
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
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
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
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
        if no_progress_guard.check(observation):
            recorder.add(
                "guard.checked",
                {
                    "step": step,
                    "guard": "no_progress",
                    "allowed": False,
                    "stale_count": no_progress_guard.stale_count,
                },
            )
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="NO_PROGRESS",
                step=step,
                detail="observations produced no new digest",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )
        recorder.add(
            "guard.checked",
            {
                "step": step,
                "guard": "no_progress",
                "allowed": True,
                "stale_count": no_progress_guard.stale_count,
            },
        )
        if cancelled is not None and cancelled():
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="CANCELLED",
                step=step,
                detail="execution was cancelled after tool response",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
            )
        if account.elapsed_ms() >= effective_limits.max_wall_time_ms:
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="WALL_TIME_BUDGET",
                step=step,
                detail="wall-clock budget expired after tool response",
                started=started,
                usage=account.usage,
                elapsed_ms=account.elapsed_ms(),
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
        usage=account.usage,
        elapsed_ms=account.elapsed_ms(),
    )


async def _close_resource(resource: object) -> None:
    """Close owned clients on every terminal path without leaking exceptions."""
    for method_name in ("aclose", "close"):
        method = getattr(resource, method_name, None)
        if not callable(method):
            continue
        try:
            result = method()
            if inspect.isawaitable(result):
                await result
        except Exception:
            pass
        return


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
    pricing: Pricing | None = None,
    require_pricing: bool = False,
    clock: Callable[[], float] = monotonic,
    prompt_variant: PromptVariant = "A",
) -> RunResult:
    """Run native calling and always close provider/tool clients afterwards."""
    try:
        return await _run_native_tool_calling(
            run_id=run_id,
            correlation_id=correlation_id,
            goal=goal,
            provider=provider,
            tool_adapter=tool_adapter,
            allowed_tools=allowed_tools,
            limits=limits,
            cancelled=cancelled,
            pricing=pricing,
            require_pricing=require_pricing,
            clock=clock,
            prompt_variant=prompt_variant,
        )
    finally:
        await _close_resource(provider)
        await _close_resource(tool_adapter)
