"""Small offline implementations of the Phase 4 pattern comparison set.

These runners share the same ``RunResult`` and explicit trace vocabulary.  A
runner receives a recorded ``ModelAdapter`` and ``ToolAdapter`` only; it has
no capability, HTTP client, Frappe session, or ERP write path.
"""

from __future__ import annotations

from time import monotonic
from typing import Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

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
    TraceRecorder,
    UsageSnapshot,
    observation_from_summary,
    validate_action_tool,
)
from agent_runtime.agent.kernel import (
    KernelLimits,
    ModelAdapter,
    RepeatGuard,
    ToolAdapter,
    ToolExecutionFailure,
    run_bounded_react,
)
from agent_runtime.evaluation.loader import AgentEvaluationCase
from agent_runtime.providers import ProviderMessage
from pydantic import Field, ValidationError

from labs.agent_patterns.react_lab import READ_TOOL_SPECS

_MODEL_PARSE_ERRORS = (ValidationError, TypeError, ValueError)


class PatternRunner(Protocol):
    """Common async interface for every lab pattern."""

    async def run(
        self,
        case: AgentEvaluationCase,
        provider: ModelAdapter,
        tool_adapter: ToolAdapter,
        limits: KernelLimits | None = None,
    ) -> RunResult: ...


class SetRepeatGuard:
    """Reference guard used by the non-learning lab runners."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def check(self, action: Action) -> bool:
        key = action.call_key()
        if key in self._seen:
            return True
        self._seen.add(key)
        return False


class _PlanAction(StrictModel):
    schema_version: Literal["1"] = "1"
    type: Literal["action"] = "action"
    step: int = Field(ge=1, le=64)
    tool_name: str = Field(min_length=1, max_length=140)
    canonical_args: dict[str, JsonValue] = Field(default_factory=dict)


class _Plan(StrictModel):
    schema_version: Literal["1"] = "1"
    type: Literal["plan"]
    summary: str = Field(min_length=1, max_length=4_000)
    final_summary: str = Field(min_length=1, max_length=4_000)
    steps: list[_PlanAction] = Field(min_length=1, max_length=6)


class _Critique(StrictModel):
    schema_version: Literal["1"] = "1"
    type: Literal["critic"]
    accepted: bool
    revision: dict[str, object] | None = None


def _context_ids(case: AgentEvaluationCase) -> tuple[UUID, UUID]:
    """Derive stable lab-only IDs so reports are reproducible."""
    seed = f"phase4-lab:{case.case_id}"
    return uuid5(NAMESPACE_URL, f"{seed}:run"), uuid5(NAMESPACE_URL, f"{seed}:correlation")


def _stop(
    *,
    recorder: TraceRecorder,
    run_id: UUID,
    code: StopCode,
    step: int,
    detail: str,
    started: float,
    final_answer: FinalAnswer | None = None,
) -> RunResult:
    reason = StopReason(
        code=code,
        step=step,
        detail=detail,
        budget_snapshot=BudgetSnapshot(
            steps=step,
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
        usage=UsageSnapshot(),
        elapsed_ms=reason.budget_snapshot.elapsed_ms,
    )


def _parse_final(raw: object) -> FinalAnswer:
    """Parse a provider final answer while accepting JSON arrays on the wire."""
    if isinstance(raw, FinalAnswer):
        return raw
    if not isinstance(raw, dict):
        raise ValueError("final answer must be an object")
    values = dict(raw)
    values.pop("type", None)
    values.pop("schema_version", None)
    values.pop("stop_reason", None)
    for field_name in ("evidence_refs", "unknowns"):
        field_value = values.get(field_name)
        if isinstance(field_value, list):
            values[field_name] = tuple(field_value)
    return FinalAnswer.model_validate(values)


class DirectRunner:
    """One model call, no tool loop."""

    async def run(
        self,
        case: AgentEvaluationCase,
        provider: ModelAdapter,
        tool_adapter: ToolAdapter,
        limits: KernelLimits | None = None,
    ) -> RunResult:
        del tool_adapter, limits
        run_id, _ = _context_ids(case)
        started = monotonic()
        recorder = TraceRecorder(run_id)
        recorder.add("run.started", {"pattern": "direct"})
        recorder.add("model.requested", {"step": 1, "tool_count": 0})
        try:
            raw = await provider.next(
                messages=(
                    ProviderMessage(role="system", content="Return one final answer."),
                    ProviderMessage(role="user", content=case.goal),
                ),
                tools=(),
                step=1,
            )
            final = _parse_final(raw)
        except _MODEL_PARSE_ERRORS:
            recorder.add("final.rejected", {"step": 1, "reason": "invalid direct answer"})
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="MODEL_ERROR",
                step=1,
                detail="direct answer could not be validated",
                started=started,
            )
        except Exception:
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="MODEL_ERROR",
                step=1,
                detail="direct provider failed",
                started=started,
            )
        recorder.add("final.proposed", {"step": 1, "evidence_refs": list(final.evidence_refs)})
        recorder.add("final.validated", {"step": 1})
        return _stop(
            recorder=recorder,
            run_id=run_id,
            code="FINAL_ANSWER",
            step=1,
            detail="direct answer accepted",
            started=started,
            final_answer=final,
        )


class ReActRunner:
    """Shared bounded ReAct implementation over the recorded adapter."""

    async def run(
        self,
        case: AgentEvaluationCase,
        provider: ModelAdapter,
        tool_adapter: ToolAdapter,
        limits: KernelLimits | None = None,
    ) -> RunResult:
        return await run_bounded_react(
            run_id=_context_ids(case)[0],
            correlation_id=_context_ids(case)[1],
            model=provider,
            tool_adapter=tool_adapter,
            allowed_tools=frozenset(case.allowed_tools),
            repeat_guard=SetRepeatGuard(),
            tools=READ_TOOL_SPECS,
            limits=limits or KernelLimits(),
            goal=case.goal,
        )


class PlanAndSolveRunner:
    """Generate one short plan, then execute its dependent read actions."""

    async def run(
        self,
        case: AgentEvaluationCase,
        provider: ModelAdapter,
        tool_adapter: ToolAdapter,
        limits: KernelLimits | None = None,
    ) -> RunResult:
        run_id, correlation_id = _context_ids(case)
        started = monotonic()
        recorder = TraceRecorder(run_id)
        recorder.add("run.started", {"pattern": "plan_and_solve"})
        recorder.add("model.requested", {"step": 1, "tool_count": len(READ_TOOL_SPECS)})
        try:
            raw = await provider.next(
                messages=(
                    ProviderMessage(role="system", content="Return one short typed plan."),
                    ProviderMessage(role="user", content=case.goal),
                ),
                tools=READ_TOOL_SPECS,
                step=1,
            )
            plan = _Plan.model_validate(raw)
        except _MODEL_PARSE_ERRORS:
            recorder.add("final.rejected", {"step": 1, "reason": "invalid plan"})
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="MODEL_ERROR",
                step=1,
                detail="plan could not be validated",
                started=started,
            )
        except Exception:
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="MODEL_ERROR",
                step=1,
                detail="plan provider failed",
                started=started,
            )

        recorder.add("model.requested", {"step": 1, "plan_steps": len(plan.steps)})
        guard: RepeatGuard = SetRepeatGuard()
        observations: list[Observation] = []
        max_steps = (limits or KernelLimits()).max_steps
        if len(plan.steps) > max_steps:
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="MAX_STEPS",
                step=0,
                detail="plan exceeds the configured step limit",
                started=started,
            )
        for step, raw_action in enumerate(plan.steps, start=1):
            recorder.add(
                "action.proposed",
                {
                    "step": step,
                    "tool_name": raw_action.tool_name,
                    "canonical_args": raw_action.canonical_args,
                },
            )
            if raw_action.step != step or raw_action.tool_name not in case.allowed_tools:
                recorder.add("action.rejected", {"step": step, "tool_name": raw_action.tool_name})
                return _stop(
                    recorder=recorder,
                    run_id=run_id,
                    code=(
                        "TOOL_NOT_ALLOWED"
                        if raw_action.tool_name not in case.allowed_tools
                        else "MODEL_ERROR"
                    ),
                    step=step,
                    detail="planned action is outside the current contract",
                    started=started,
                )
            try:
                action = Action(
                    step=step,
                    tool_name=raw_action.tool_name,
                    canonical_args=raw_action.canonical_args,
                    correlation_id=correlation_id,
                )
                validate_action_tool(action)
            except _MODEL_PARSE_ERRORS:
                recorder.add("action.rejected", {"step": step, "reason": "invalid tool arguments"})
                return _stop(
                    recorder=recorder,
                    run_id=run_id,
                    code="INVALID_TOOL_ARGS",
                    step=step,
                    detail="planned action failed typed validation",
                    started=started,
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
                    detail="planned action repeats a previous call",
                    started=started,
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
                observations.append(observation)
                recorder.add(
                    "tool.failed",
                    {
                        "step": step,
                        "tool_name": action.tool_name,
                        "digest": observation.digest,
                        "error_code": error.code,
                    },
                )
                return _stop(
                    recorder=recorder,
                    run_id=run_id,
                    code="TOOL_ERROR",
                    step=step,
                    detail="planned tool returned a classified failure",
                    started=started,
                )
            except Exception:
                return _stop(
                    recorder=recorder,
                    run_id=run_id,
                    code="TOOL_ERROR",
                    step=step,
                    detail="planned tool adapter failed",
                    started=started,
                )
            if observation.tool_name != action.tool_name:
                return _stop(
                    recorder=recorder,
                    run_id=run_id,
                    code="TOOL_ERROR",
                    step=step,
                    detail="tool observation name did not match action",
                    started=started,
                )
            observation = observation.model_copy(update={"step": step})
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
        evidence_refs = tuple(observation.digest for observation in observations if observation.ok)
        final = FinalAnswer(
            status="SUCCEEDED",
            summary=plan.final_summary,
            evidence_refs=evidence_refs,
        )
        recorder.add(
            "final.proposed",
            {"step": len(plan.steps), "evidence_refs": list(evidence_refs)},
        )
        recorder.add("final.validated", {"step": len(plan.steps)})
        return _stop(
            recorder=recorder,
            run_id=run_id,
            code="FINAL_ANSWER",
            step=len(plan.steps),
            detail="short plan completed",
            started=started,
            final_answer=final,
        )


class ReflectionRunner:
    """Base answer plus at most one critic/revise call."""

    async def run(
        self,
        case: AgentEvaluationCase,
        provider: ModelAdapter,
        tool_adapter: ToolAdapter,
        limits: KernelLimits | None = None,
    ) -> RunResult:
        del tool_adapter, limits
        run_id, _ = _context_ids(case)
        started = monotonic()
        recorder = TraceRecorder(run_id)
        recorder.add("run.started", {"pattern": "reflection", "max_reflections": 1})
        messages = (
            ProviderMessage(role="system", content="Return one final answer."),
            ProviderMessage(role="user", content=case.goal),
        )
        try:
            recorder.add("model.requested", {"step": 1, "purpose": "draft"})
            draft = _parse_final(await provider.next(messages=messages, tools=(), step=1))
            recorder.add("final.proposed", {"step": 1, "evidence_refs": list(draft.evidence_refs)})
            critic_messages = (
                *messages,
                ProviderMessage(role="assistant", content=draft.summary),
                ProviderMessage(
                    role="user",
                    content="Critique once and return a revised final answer or acceptance.",
                ),
            )
            recorder.add("model.requested", {"step": 2, "purpose": "critic_or_revision"})
            second = await provider.next(messages=critic_messages, tools=(), step=2)
            if isinstance(second, dict) and second.get("type") == "critic":
                critique = _Critique.model_validate(second)
                final = (
                    draft
                    if critique.accepted or critique.revision is None
                    else _parse_final(critique.revision)
                )
            else:
                final = _parse_final(second)
        except _MODEL_PARSE_ERRORS:
            recorder.add("final.rejected", {"step": 2, "reason": "invalid reflection response"})
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="MODEL_ERROR",
                step=2,
                detail="reflection response could not be validated",
                started=started,
            )
        except Exception:
            return _stop(
                recorder=recorder,
                run_id=run_id,
                code="MODEL_ERROR",
                step=2,
                detail="reflection provider failed",
                started=started,
            )
        recorder.add("final.proposed", {"step": 2, "evidence_refs": list(final.evidence_refs)})
        recorder.add("final.validated", {"step": 2})
        return _stop(
            recorder=recorder,
            run_id=run_id,
            code="FINAL_ANSWER",
            step=2,
            detail="reflection completed with one critic pass",
            started=started,
            final_answer=final,
        )


class MiniStepAgent(ReActRunner):
    """Purchasing-oriented multi-step runner with the shared bounded kernel."""

    async def run(
        self,
        case: AgentEvaluationCase,
        provider: ModelAdapter,
        tool_adapter: ToolAdapter,
        limits: KernelLimits | None = None,
    ) -> RunResult:
        return await super().run(
            case,
            provider,
            tool_adapter,
            limits=limits or KernelLimits(max_steps=6),
        )


# Friendly aliases used by comparison code and notebooks.
Direct = DirectRunner
BoundedReAct = ReActRunner
PlanAndSolve = PlanAndSolveRunner
Reflection = ReflectionRunner
