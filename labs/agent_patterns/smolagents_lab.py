"""Lab-only adapter for the approved smolagents ToolCallingAgent snapshot.

The business Runtime must not import this module.  It exists to compare a
third-party execution loop with Synora's contracts while all tool calls still
go through a recorded, read-only adapter.  No capability, HTTP client, ERP
response, or hidden model reasoning is passed to smolagents.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol, cast
from uuid import UUID

from agent_runtime.agent.contracts import (
    Action,
    BudgetSnapshot,
    FinalAnswer,
    JsonValue,
    Observation,
    RunResult,
    StopCode,
    StopReason,
    ToolName,
    TraceRecorder,
    UsageSnapshot,
    observation_from_summary,
    validate_action_tool,
)
from agent_runtime.agent.kernel import ToolAdapter, ToolExecutionFailure
from agent_runtime.agent.native_tool_calling import provider_tool_specs
from agent_runtime.evaluation.loader import AgentEvaluationCase
from pydantic import BaseModel, ConfigDict, Field, ValidationError

SMOLAGENTS_COMMIT = "30bb1161095dbae2271e6bc3cc4c219cc3897a57"
SMOLAGENTS_ENTRYPOINTS = (
    "smolagents.agents.MultiStepAgent",
    "smolagents.agents.ToolCallingAgent",
    "smolagents.memory.ActionStep",
    "smolagents.memory.AgentMemory",
)

# smolagents requires Python identifiers for Tool.name, while Synora's
# canonical names intentionally use dots.  The adapter keeps that translation
# local and never changes the public Gateway names.
SMOL_TOOL_ALIASES: dict[ToolName, str] = {
    "item.lookup": "item_lookup",
    "supplier.lookup": "supplier_lookup",
    "stock.projected": "stock_projected",
    "demand.open": "demand_open",
    "material_request.open": "material_request_open",
    "purchase_order.open": "purchase_order_open",
}
SMOL_ALIAS_TO_TOOL: dict[str, ToolName] = {alias: name for name, alias in SMOL_TOOL_ALIASES.items()}
_SMOL_FINAL_TOOL = "final_answer"


class SmolagentsLimits(BaseModel):
    """Comparison limits; production budget enforcement remains P4.4."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    max_steps: int = Field(default=6, ge=1, le=64)


class SmolagentsAgent(Protocol):
    def run(
        self,
        task: str,
        *,
        reset: bool,
        max_steps: int,
        return_full_result: bool,
    ) -> object: ...


@dataclass(frozen=True)
class RecordedSmolagentsCall:
    """One call captured by a read-only smolagents Tool wrapper."""

    step: int
    provider_tool_call_id: str
    tool_name: ToolName
    canonical_args: dict[str, JsonValue]
    observation: Observation | None = None
    error_code: str | None = None


class SmolagentsToolError(Exception):
    """Safe error surfaced to the lab framework without raw tool data."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RecordedSmolagentsToolLedger:
    """Synchronously expose only an async recorded ToolAdapter to smolagents."""

    def __init__(
        self,
        *,
        run_id: UUID,
        correlation_id: UUID,
        tool_adapter: ToolAdapter,
        allowed_tools: frozenset[ToolName],
    ) -> None:
        unknown = set(allowed_tools).difference(SMOL_TOOL_ALIASES)
        if unknown:
            raise ValueError("smolagents adapter received an unknown tool allowlist")
        self._run_id = run_id
        self._correlation_id = correlation_id
        self._tool_adapter = tool_adapter
        self._allowed_tools = allowed_tools
        self._seen: set[str] = set()
        self.calls: list[RecordedSmolagentsCall] = []

    def invoke(self, tool_name: ToolName, arguments: Mapping[str, object]) -> str:
        """Execute one recorded read tool and return only its bounded summary."""
        if tool_name not in self._allowed_tools:
            self.calls.append(
                RecordedSmolagentsCall(
                    step=len(self.calls) + 1,
                    provider_tool_call_id=f"smol-call-{len(self.calls) + 1}",
                    tool_name=tool_name,
                    canonical_args={},
                    error_code="TOOL_NOT_ALLOWED",
                )
            )
            raise SmolagentsToolError("TOOL_NOT_ALLOWED")

        step = len(self.calls) + 1
        call_id = f"smol-call-{step}"
        raw_args = dict(arguments)
        try:
            action = Action(
                step=step,
                tool_name=tool_name,
                canonical_args=cast(dict[str, JsonValue], raw_args),
                correlation_id=self._correlation_id,
            )
            validate_action_tool(action)
        except (ValidationError, TypeError, ValueError) as error:
            del error
            self.calls.append(
                RecordedSmolagentsCall(
                    step=step,
                    provider_tool_call_id=call_id,
                    tool_name=tool_name,
                    canonical_args={},
                    error_code="INVALID_TOOL_ARGS",
                )
            )
            raise SmolagentsToolError("INVALID_TOOL_ARGS") from None

        if action.call_key() in self._seen:
            self.calls.append(
                RecordedSmolagentsCall(
                    step=step,
                    provider_tool_call_id=call_id,
                    tool_name=tool_name,
                    canonical_args=action.canonical_args,
                    error_code="REPEATED_CALL",
                )
            )
            raise SmolagentsToolError("REPEATED_CALL")
        self._seen.add(action.call_key())

        try:
            pending = self._tool_adapter.execute(action)
            observation = _resolve_sync(pending)
            if not isinstance(observation, Observation):
                raise TypeError("recorded adapter returned a non-Observation")
        except ToolExecutionFailure as error:
            self.calls.append(
                RecordedSmolagentsCall(
                    step=step,
                    provider_tool_call_id=call_id,
                    tool_name=tool_name,
                    canonical_args=action.canonical_args,
                    error_code=error.code,
                )
            )
            raise SmolagentsToolError("TOOL_ERROR") from None
        except Exception:
            self.calls.append(
                RecordedSmolagentsCall(
                    step=step,
                    provider_tool_call_id=call_id,
                    tool_name=tool_name,
                    canonical_args=action.canonical_args,
                    error_code="TOOL_ERROR",
                )
            )
            raise SmolagentsToolError("TOOL_ERROR") from None

        if observation.tool_name != action.tool_name or observation.step != step:
            self.calls.append(
                RecordedSmolagentsCall(
                    step=step,
                    provider_tool_call_id=call_id,
                    tool_name=tool_name,
                    canonical_args=action.canonical_args,
                    error_code="TOOL_ERROR",
                )
            )
            raise SmolagentsToolError("TOOL_ERROR")

        self.calls.append(
            RecordedSmolagentsCall(
                step=step,
                provider_tool_call_id=call_id,
                tool_name=tool_name,
                canonical_args=action.canonical_args,
                observation=observation,
            )
        )
        return cast(str, cast(Observation, observation).summary)


@dataclass(frozen=True)
class RecordedSmolagentsToolSet:
    """Tools plus the ledger needed to adapt the resulting trace."""

    tools: tuple[object, ...]
    ledger: RecordedSmolagentsToolLedger


def _resolve_sync(value: object) -> object:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(cast(Any, value))
    raise RuntimeError("smolagents lab tools must run outside an active event loop")


def _smol_input_schema(parameters: dict[str, object]) -> dict[str, dict[str, str]]:
    properties = parameters.get("properties", {})
    if not isinstance(properties, dict):
        return {}
    converted: dict[str, dict[str, str]] = {}
    for key, raw_schema in properties.items():
        if not isinstance(key, str) or not isinstance(raw_schema, dict):
            continue
        raw_type = raw_schema.get("type", "string")
        if isinstance(raw_type, list):
            raw_type = next((item for item in raw_type if item != "null"), "string")
        type_name = raw_type if isinstance(raw_type, str) else "string"
        if type_name not in {"string", "boolean", "integer", "number", "object", "array"}:
            type_name = "string"
        description = raw_schema.get("description", "Recorded read-only input")
        converted[key] = {
            "type": type_name,
            "description": (
                description if isinstance(description, str) else "Recorded read-only input"
            ),
        }
    return converted


def build_recorded_smolagents_tools(
    *,
    run_id: UUID,
    correlation_id: UUID,
    tool_adapter: ToolAdapter,
    allowed_tools: frozenset[ToolName],
) -> RecordedSmolagentsToolSet:
    """Build smolagents ``Tool`` wrappers around the recorded adapter only."""
    try:
        from smolagents import Tool
    except ImportError as error:
        raise RuntimeError(
            "install the lab dependency group to use the smolagents comparison"
        ) from error

    specs = {spec.name: spec for spec in provider_tool_specs(allowed_tools)}
    ledger = RecordedSmolagentsToolLedger(
        run_id=run_id,
        correlation_id=correlation_id,
        tool_adapter=tool_adapter,
        allowed_tools=allowed_tools,
    )

    def make_tool(canonical_name: ToolName, alias: str) -> object:
        parameters = specs[canonical_name].parameters

        class RecordedTool(Tool):  # type: ignore[misc]
            name = alias
            description = "Recorded read-only ERP observation; no writes"
            inputs = _smol_input_schema(parameters)
            output_type = "string"
            # A dynamic ``**kwargs`` forward method cannot expose each schema
            # field in its Python signature; the provider/Gateway schema still
            # validates the actual Action before the adapter executes it.
            skip_forward_signature_validation = True

            def forward(self, **kwargs: object) -> str:
                return ledger.invoke(canonical_name, kwargs)

        return RecordedTool()

    tools = tuple(
        make_tool(name, SMOL_TOOL_ALIASES[name])
        for name in SMOL_TOOL_ALIASES
        if name in allowed_tools
    )
    return RecordedSmolagentsToolSet(tools=tools, ledger=ledger)


def build_smolagents_tool_calling_agent(
    *,
    model: object,
    recorded_tools: RecordedSmolagentsToolSet,
    limits: SmolagentsLimits | None = None,
) -> SmolagentsAgent:
    """Instantiate the pinned ToolCallingAgent with serial recorded tools."""
    try:
        from smolagents import ToolCallingAgent
    except ImportError as error:
        raise RuntimeError(
            "install the lab dependency group to use the smolagents comparison"
        ) from error
    effective_limits = limits or SmolagentsLimits()
    return cast(
        SmolagentsAgent,
        ToolCallingAgent(
            tools=list(recorded_tools.tools),
            model=model,
            max_steps=effective_limits.max_steps,
            max_tool_threads=1,
            add_base_tools=False,
            return_full_result=True,
        ),
    )


def _json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parse_final(output: object) -> FinalAnswer:
    if isinstance(output, FinalAnswer):
        return output
    raw: object = output
    if isinstance(output, str):
        raw = json.loads(
            output,
            parse_constant=_json_constant,
            object_pairs_hook=_unique_pairs,
        )
    if not isinstance(raw, dict):
        raise ValueError("smolagents output must be typed final JSON")
    values = dict(raw)
    values.pop("type", None)
    values.pop("schema_version", None)
    values.pop("stop_reason", None)
    for field_name in ("evidence_refs", "unknowns"):
        if isinstance(values.get(field_name), list):
            values[field_name] = tuple(values[field_name])
    return FinalAnswer.model_validate(values)


def _smol_usage(result: object) -> UsageSnapshot:
    token_usage = getattr(result, "token_usage", None)
    if token_usage is None:
        return UsageSnapshot()
    prompt_tokens = getattr(token_usage, "input_tokens", 0)
    completion_tokens = getattr(token_usage, "output_tokens", 0)
    if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
        return UsageSnapshot()
    if prompt_tokens < 0 or completion_tokens < 0:
        return UsageSnapshot()
    return UsageSnapshot(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def _smol_elapsed(result: object, started: float) -> int:
    timing = getattr(result, "timing", None)
    start_time = getattr(timing, "start_time", None)
    end_time = getattr(timing, "end_time", None)
    if isinstance(start_time, (int, float)) and isinstance(end_time, (int, float)):
        return max(0, int((end_time - start_time) * 1000))
    return max(0, int((monotonic() - started) * 1000))


def _step_tool_names(result: object) -> tuple[tuple[str, ...], bool]:
    """Read only tool names/count from serialized smolagents steps."""
    names: list[str] = []
    parallel = False
    steps = getattr(result, "steps", ())
    if not isinstance(steps, (list, tuple)):
        return (), False
    for step in steps:
        if not isinstance(step, dict):
            continue
        calls = step.get("tool_calls")
        if not isinstance(calls, list):
            continue
        if len(calls) > 1:
            parallel = True
        for call in calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if isinstance(function, dict) and isinstance(function.get("name"), str):
                names.append(function["name"])
            elif isinstance(call.get("name"), str):
                names.append(call["name"])
    return tuple(names), parallel


def _stop(
    *,
    recorder: TraceRecorder,
    code: StopCode,
    step: int,
    detail: str,
    started: float,
    usage: UsageSnapshot,
    elapsed_ms: int | None = None,
    final_answer: FinalAnswer | None = None,
) -> RunResult:
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


def run_smolagents_tool_calling(
    *,
    case: AgentEvaluationCase,
    run_id: UUID,
    correlation_id: UUID,
    agent: SmolagentsAgent,
    recorded_tools: RecordedSmolagentsToolSet,
    limits: SmolagentsLimits | None = None,
) -> RunResult:
    """Run a lab agent and translate its safe ledger into Synora ``RunResult``."""
    effective_limits = limits or SmolagentsLimits()
    started = monotonic()
    recorder = TraceRecorder(run_id)
    recorder.add(
        "run.started",
        {
            "execution_mode": "AGENT",
            "pattern": "smolagents",
            "smolagents_commit": SMOLAGENTS_COMMIT[:12],
            "max_steps": effective_limits.max_steps,
        },
    )

    expected_aliases = {
        SMOL_TOOL_ALIASES[name] for name in case.allowed_tools if name in SMOL_TOOL_ALIASES
    }
    configured_names = {str(getattr(tool, "name", "")) for tool in recorded_tools.tools}
    if configured_names and not configured_names.issubset(expected_aliases):
        return _stop(
            recorder=recorder,
            code="TOOL_NOT_ALLOWED",
            step=0,
            detail="smolagents tool set exceeds the case allowlist",
            started=started,
            usage=UsageSnapshot(),
        )

    recorder.add("model.requested", {"step": 1, "tool_count": len(configured_names)})
    result: object
    try:
        result = agent.run(
            case.goal,
            reset=True,
            max_steps=effective_limits.max_steps,
            return_full_result=True,
        )
    except Exception:
        usage = UsageSnapshot()
        if recorded_tools.ledger.calls:
            step = recorded_tools.ledger.calls[-1].step
        else:
            step = 1
        return _stop(
            recorder=recorder,
            code=_failure_code(recorded_tools.ledger),
            step=step,
            detail="smolagents agent failed with a safe classified error",
            started=started,
            usage=usage,
        )

    usage = _smol_usage(result)
    names, parallel = _step_tool_names(result)
    if parallel:
        recorder.add("action.rejected", {"step": 1, "reason": "parallel tool calls"})
        return _stop(
            recorder=recorder,
            code="MODEL_ERROR",
            step=1,
            detail="smolagents parallel tool calls are not part of Phase 4",
            started=started,
            usage=usage,
            elapsed_ms=_smol_elapsed(result, started),
        )
    unknown = set(names).difference(expected_aliases, {_SMOL_FINAL_TOOL})
    if unknown:
        recorder.add("action.rejected", {"step": 1, "reason": "tool is not allowed"})
        return _stop(
            recorder=recorder,
            code="TOOL_NOT_ALLOWED",
            step=1,
            detail="smolagents returned a tool outside the case allowlist",
            started=started,
            usage=usage,
            elapsed_ms=_smol_elapsed(result, started),
        )

    for call in recorded_tools.ledger.calls:
        recorder.add("model.requested", {"step": call.step, "tool_count": len(configured_names)})
        recorder.add(
            "action.proposed",
            {
                "step": call.step,
                "tool_name": call.tool_name,
                "canonical_args": call.canonical_args,
            },
        )
        if call.error_code is not None:
            if call.error_code == "REPEATED_CALL":
                recorder.add(
                    "guard.checked",
                    {"step": call.step, "guard": "repeated_call", "allowed": False},
                )
                return _stop(
                    recorder=recorder,
                    code="REPEATED_CALL",
                    step=call.step,
                    detail="smolagents repeated a canonical tool call",
                    started=started,
                    usage=usage,
                    elapsed_ms=_smol_elapsed(result, started),
                )
            recorder.add("action.rejected", {"step": call.step, "reason": call.error_code})
            code: StopCode = (
                "INVALID_TOOL_ARGS" if call.error_code == "INVALID_TOOL_ARGS" else "TOOL_ERROR"
            )
            return _stop(
                recorder=recorder,
                code=code,
                step=call.step,
                detail="smolagents recorded tool call was rejected",
                started=started,
                usage=usage,
                elapsed_ms=_smol_elapsed(result, started),
            )
        recorder.add("action.validated", {"step": call.step, "tool_name": call.tool_name})
        recorder.add(
            "guard.checked",
            {"step": call.step, "guard": "repeated_call", "allowed": True},
        )
        recorder.add("tool.started", {"step": call.step, "tool_name": call.tool_name})
        assert call.observation is not None
        recorder.add(
            "tool.observed",
            {
                "step": call.step,
                "tool_name": call.tool_name,
                "ok": call.observation.ok,
                "digest": call.observation.digest,
                "summary": call.observation.summary,
            },
        )

    output = getattr(result, "output", None)
    try:
        final = _parse_final(output)
    except TypeError, ValueError, ValidationError:
        state = getattr(result, "state", "")
        final_code: StopCode = (
            "MAX_STEPS" if state == "max_steps_error" else "UNSUPPORTED_FINAL_ANSWER"
        )
        recorder.add(
            "final.rejected",
            {"step": len(recorded_tools.ledger.calls) + 1, "reason": final_code},
        )
        return _stop(
            recorder=recorder,
            code=final_code,
            step=len(recorded_tools.ledger.calls) + 1,
            detail="smolagents output was not a typed evidence-backed final answer",
            started=started,
            usage=usage,
            elapsed_ms=_smol_elapsed(result, started),
        )

    recorder.add(
        "final.proposed",
        {"step": len(recorded_tools.ledger.calls) + 1, "evidence_refs": list(final.evidence_refs)},
    )
    known_digests = {
        call.observation.digest
        for call in recorded_tools.ledger.calls
        if call.observation is not None and call.observation.ok
    }
    if not final.evidence_refs or not set(final.evidence_refs).issubset(known_digests):
        recorder.add(
            "final.rejected",
            {"step": len(recorded_tools.ledger.calls) + 1, "reason": "evidence ref is unknown"},
        )
        return _stop(
            recorder=recorder,
            code="UNSUPPORTED_FINAL_ANSWER",
            step=len(recorded_tools.ledger.calls) + 1,
            detail="smolagents final answer did not cite a recorded Observation digest",
            started=started,
            usage=usage,
            elapsed_ms=_smol_elapsed(result, started),
        )
    recorder.add("final.validated", {"step": len(recorded_tools.ledger.calls) + 1})
    return _stop(
        recorder=recorder,
        code="FINAL_ANSWER",
        step=len(recorded_tools.ledger.calls) + 1,
        detail="smolagents result adapted with evidence validation",
        started=started,
        usage=usage,
        elapsed_ms=_smol_elapsed(result, started),
        final_answer=final,
    )


def _failure_code(ledger: RecordedSmolagentsToolLedger) -> StopCode:
    if not ledger.calls:
        return "MODEL_ERROR"
    code = ledger.calls[-1].error_code
    if code == "REPEATED_CALL":
        return "REPEATED_CALL"
    if code == "INVALID_TOOL_ARGS":
        return "INVALID_TOOL_ARGS"
    if code == "TOOL_NOT_ALLOWED":
        return "TOOL_NOT_ALLOWED"
    if code is not None:
        return "TOOL_ERROR"
    return "MODEL_ERROR"


def _observation(
    *,
    run_id: UUID,
    step: int,
    tool_name: ToolName,
    summary: str,
) -> Observation:
    """Small helper for lab fixtures; production observations come from Gateway."""
    return observation_from_summary(
        run_id=run_id,
        step=step,
        tool_name=tool_name,
        ok=True,
        summary=summary,
    )
