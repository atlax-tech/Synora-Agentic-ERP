"""Small, explicit Phase 4 execution kernel.

The kernel owns the order of model -> validation -> tool -> observation ->
stop.  It accepts untrusted model dictionaries and never lets a model choose
outside the caller-provided read-tool allowlist.  Durable workflow and ERP
business decisions intentionally live outside this module.
"""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import Field, ValidationError

from agent_runtime.agent.contracts import (
    Action,
    BudgetSnapshot,
    FinalAnswer,
    JsonValue,
    Observation,
    RunResult,
    StopReason,
    StrictModel,
    ToolName,
    TraceRecorder,
    UsageSnapshot,
    observation_from_summary,
    validate_action_tool,
)
from agent_runtime.agent.guards import NoProgressGuard, ToolFrequencyGuard
from agent_runtime.providers import ProviderMessage, ProviderToolSpec


class KernelLimits(StrictModel):
    """P4.2 limits; P4.4 adds the full token/cost/time policy."""

    max_steps: int = Field(default=6, ge=1, le=64)
    max_wall_time_ms: int = Field(default=180_000, ge=1, le=180_000)
    max_calls_per_tool: int = Field(default=3, ge=1, le=3)
    no_progress_threshold: int = Field(default=2, ge=1, le=2)


class ModelAdapter(Protocol):
    async def next(
        self,
        *,
        messages: tuple[ProviderMessage, ...],
        tools: tuple[ProviderToolSpec, ...],
        step: int,
    ) -> object: ...


class ToolAdapter(Protocol):
    async def execute(self, action: Action) -> Observation: ...


class RepeatGuard(Protocol):
    def check(self, action: Action) -> bool: ...


class ToolExecutionFailure(Exception):
    """Safe, classified tool failure used by recorded and real adapters."""

    def __init__(self, code: str = "TOOL_FAILED", *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class _RawAction(StrictModel):
    schema_version: Literal["1"] = "1"
    type: Literal["action"]
    step: int = Field(ge=1, le=64)
    tool_name: str = Field(min_length=1, max_length=140)
    canonical_args: dict[str, JsonValue] = Field(default_factory=dict)
    correlation_id: Annotated[UUID, Field(strict=False)]


class _RawFinal(StrictModel):
    schema_version: Literal["1"] = "1"
    type: Literal["final"]
    status: Literal["SUCCEEDED", "NEEDS_INPUT", "FAILED"]
    summary: str = Field(min_length=1, max_length=4_000)
    # Provider JSON arrays arrive as Python lists; convert them to the public
    # immutable tuple contract only after this strict wire-level parse.
    evidence_refs: list[str] = Field(default_factory=list, max_length=32)
    unknowns: list[str] = Field(default_factory=list, max_length=32)


def _snapshot(step: int, started: float) -> BudgetSnapshot:
    return BudgetSnapshot(steps=step, elapsed_ms=max(0, int((monotonic() - started) * 1000)))


def _stop(
    *,
    recorder: TraceRecorder,
    mode: Literal["DETERMINISTIC", "AGENT"],
    code: Literal[
        "FINAL_ANSWER",
        "MAX_STEPS",
        "REPEATED_CALL",
        "NO_PROGRESS",
        "TOKEN_BUDGET",
        "COST_BUDGET",
        "WALL_TIME_BUDGET",
        "CANCELLED",
        "TOOL_NOT_ALLOWED",
        "TOOL_FREQUENCY",
        "INVALID_TOOL_ARGS",
        "TOOL_ERROR",
        "MODEL_ERROR",
        "UNSUPPORTED_FINAL_ANSWER",
    ],
    step: int,
    detail: str,
    started: float,
    final_answer: FinalAnswer | None = None,
) -> RunResult:
    reason = StopReason(
        code=code,
        step=step,
        detail=detail,
        budget_snapshot=_snapshot(step, started),
    )
    if final_answer is not None:
        final_answer = final_answer.model_copy(update={"stop_reason": reason})
    recorder.add("run.stopped", {"code": code, "step": step, "detail": detail})
    return RunResult(
        execution_mode=mode,
        final_answer=final_answer,
        stop_reason=reason,
        events=recorder.events(),
        usage=UsageSnapshot(),
        elapsed_ms=reason.budget_snapshot.elapsed_ms,
    )


def _decode(raw: object, *, correlation_id: UUID, step: int) -> Action | FinalAnswer | _RawAction:
    """Parse one untrusted model result without exposing validation input."""
    if isinstance(raw, (Action, FinalAnswer)):
        decision: Action | FinalAnswer | _RawAction = raw
    elif isinstance(raw, dict):
        kind = raw.get("type")
        if kind == "action":
            parsed = _RawAction.model_validate(raw)
            if parsed.correlation_id != correlation_id or parsed.step != step:
                raise ValueError("model action context mismatch")
            # Keep the raw tool name and arguments until the kernel applies
            # the caller's allowlist and the existing Gateway schema.
            decision = parsed
        elif kind == "final":
            parsed_final = _RawFinal.model_validate(raw)
            decision = FinalAnswer(
                status=parsed_final.status,
                summary=parsed_final.summary,
                evidence_refs=tuple(parsed_final.evidence_refs),
                unknowns=tuple(parsed_final.unknowns),
            )
        else:
            raise ValueError("model decision type is invalid")
    else:
        raise ValueError("model decision is invalid")
    if isinstance(decision, Action) and decision.correlation_id != correlation_id:
        raise ValueError("model action correlation mismatch")
    return decision


async def run_bounded_react(
    *,
    run_id: UUID,
    correlation_id: UUID,
    model: ModelAdapter,
    tool_adapter: ToolAdapter,
    allowed_tools: frozenset[ToolName],
    repeat_guard: RepeatGuard,
    tools: tuple[ProviderToolSpec, ...] = (),
    limits: KernelLimits | None = None,
    cancelled: Callable[[], bool] | None = None,
    goal: str = "Complete the supplied procurement goal.",
    no_progress_guard: NoProgressGuard | None = None,
    tool_frequency_guard: ToolFrequencyGuard | None = None,
) -> RunResult:
    """Run a short prompt-oriented ReAct loop over typed read tools."""
    effective_limits = limits or KernelLimits()
    started = monotonic()
    recorder = TraceRecorder(run_id)
    recorder.add(
        "run.started",
        {"execution_mode": "AGENT", "max_steps": effective_limits.max_steps},
    )
    messages: list[ProviderMessage] = [
        ProviderMessage(role="system", content="Return one typed action or final answer."),
        ProviderMessage(role="user", content=goal),
    ]
    observations: list[Observation] = []
    progress_guard = no_progress_guard or NoProgressGuard(
        threshold=effective_limits.no_progress_threshold
    )
    frequency_guard = tool_frequency_guard or ToolFrequencyGuard(
        max_calls_per_tool=effective_limits.max_calls_per_tool
    )

    for step in range(1, effective_limits.max_steps + 1):
        if cancelled is not None and cancelled():
            return _stop(
                recorder=recorder,
                mode="AGENT",
                code="CANCELLED",
                step=step,
                detail="execution was cancelled",
                started=started,
            )
        if max(0, int((monotonic() - started) * 1000)) >= effective_limits.max_wall_time_ms:
            return _stop(
                recorder=recorder,
                mode="AGENT",
                code="WALL_TIME_BUDGET",
                step=step,
                detail="wall-clock budget expired before model call",
                started=started,
            )
        recorder.add("model.requested", {"step": step, "tool_count": len(tools)})
        try:
            raw = await model.next(messages=tuple(messages), tools=tools, step=step)
            decision = _decode(raw, correlation_id=correlation_id, step=step)
            if cancelled is not None and cancelled():
                return _stop(
                    recorder=recorder,
                    mode="AGENT",
                    code="CANCELLED",
                    step=step,
                    detail="execution was cancelled after model response",
                    started=started,
                )
        except ValidationError, TypeError, ValueError:
            recorder.add("final.rejected", {"step": step, "reason": "invalid model decision"})
            return _stop(
                recorder=recorder,
                mode="AGENT",
                code="MODEL_ERROR",
                step=step,
                detail="model decision could not be validated",
                started=started,
            )
        except Exception:
            return _stop(
                recorder=recorder,
                mode="AGENT",
                code="MODEL_ERROR",
                step=step,
                detail="model adapter failed",
                started=started,
            )

        if isinstance(decision, FinalAnswer):
            recorder.add(
                "final.proposed",
                {"step": step, "evidence_refs": list(decision.evidence_refs)},
            )
            known_digests = {observation.digest for observation in observations if observation.ok}
            if not decision.evidence_refs or not set(decision.evidence_refs).issubset(
                known_digests
            ):
                recorder.add("final.rejected", {"step": step, "reason": "evidence ref is unknown"})
                return _stop(
                    recorder=recorder,
                    mode="AGENT",
                    code="UNSUPPORTED_FINAL_ANSWER",
                    step=step,
                    detail="final answer did not cite an observed digest",
                    started=started,
                )
            recorder.add("final.validated", {"step": step})
            return _stop(
                recorder=recorder,
                mode="AGENT",
                code="FINAL_ANSWER",
                step=step,
                detail="validated final answer",
                started=started,
                final_answer=decision,
            )

        # Unknown tool names are deliberately rejected before typed Action
        # construction, so a model cannot expand the closed union.
        raw_tool_name = getattr(decision, "tool_name", "")
        if raw_tool_name not in allowed_tools:
            recorder.add("action.rejected", {"step": step, "tool_name": str(raw_tool_name)})
            return _stop(
                recorder=recorder,
                mode="AGENT",
                code="TOOL_NOT_ALLOWED",
                step=step,
                detail="tool is outside the current allowlist",
                started=started,
            )
        if isinstance(decision, _RawAction):
            try:
                action = Action.model_validate(
                    decision.model_dump(exclude={"schema_version", "type"})
                )
            except ValidationError:
                recorder.add(
                    "action.rejected",
                    {"step": step, "reason": "tool arguments are invalid"},
                )
                return _stop(
                    recorder=recorder,
                    mode="AGENT",
                    code="INVALID_TOOL_ARGS",
                    step=step,
                    detail="known tool action failed typed validation",
                    started=started,
                )
        elif isinstance(decision, Action):
            action = decision
        else:
            recorder.add("action.rejected", {"step": step, "reason": "tool arguments are invalid"})
            return _stop(
                recorder=recorder,
                mode="AGENT",
                code="INVALID_TOOL_ARGS",
                step=step,
                detail="known tool action failed typed validation",
                started=started,
            )
        recorder.add(
            "action.proposed",
            {"step": step, "tool_name": action.tool_name, "canonical_args": action.canonical_args},
        )
        try:
            validate_action_tool(action)
        except ValidationError:
            recorder.add("action.rejected", {"step": step, "reason": "tool arguments are invalid"})
            return _stop(
                recorder=recorder,
                mode="AGENT",
                code="INVALID_TOOL_ARGS",
                step=step,
                detail="tool arguments failed the existing gateway schema",
                started=started,
            )
        recorder.add("action.validated", {"step": step, "tool_name": action.tool_name})
        if repeat_guard.check(action):
            recorder.add(
                "guard.checked",
                {"step": step, "guard": "repeated_call", "allowed": False},
            )
            return _stop(
                recorder=recorder,
                mode="AGENT",
                code="REPEATED_CALL",
                step=step,
                detail="the same tool and canonical arguments were already used",
                started=started,
            )
        recorder.add("guard.checked", {"step": step, "guard": "repeated_call", "allowed": True})
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
                mode="AGENT",
                code="TOOL_FREQUENCY",
                step=step,
                detail="the same tool exceeded its per-run call frequency",
                started=started,
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
                {
                    "step": step,
                    "tool_name": action.tool_name,
                    "error_code": error.code,
                },
            )
            observations.append(observation)
            return _stop(
                recorder=recorder,
                mode="AGENT",
                code="TOOL_ERROR",
                step=step,
                detail="tool adapter returned a classified failure",
                started=started,
            )
        except Exception:
            recorder.add("tool.failed", {"step": step, "tool_name": action.tool_name})
            return _stop(
                recorder=recorder,
                mode="AGENT",
                code="TOOL_ERROR",
                step=step,
                detail="tool adapter failed",
                started=started,
            )
        if cancelled is not None and cancelled():
            return _stop(
                recorder=recorder,
                mode="AGENT",
                code="CANCELLED",
                step=step,
                detail="execution was cancelled after tool response",
                started=started,
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
                mode="AGENT",
                code="TOOL_ERROR",
                step=step,
                detail="tool observation did not match the action context",
                started=started,
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
        if progress_guard.check(observation):
            recorder.add(
                "guard.checked",
                {
                    "step": step,
                    "guard": "no_progress",
                    "allowed": False,
                    "stale_count": progress_guard.stale_count,
                },
            )
            return _stop(
                recorder=recorder,
                mode="AGENT",
                code="NO_PROGRESS",
                step=step,
                detail="observations produced no new digest",
                started=started,
            )
        recorder.add(
            "guard.checked",
            {
                "step": step,
                "guard": "no_progress",
                "allowed": True,
                "stale_count": progress_guard.stale_count,
            },
        )
        messages.append(
            ProviderMessage(
                role="user",
                content=f"Observation digest={observation.digest}; summary={observation.summary}",
            )
        )

    return _stop(
        recorder=recorder,
        mode="AGENT",
        code="MAX_STEPS",
        step=effective_limits.max_steps,
        detail="maximum execution steps reached",
        started=started,
    )
