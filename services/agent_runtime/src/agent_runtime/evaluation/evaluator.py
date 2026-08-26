"""Pure evaluation functions for Phase 4 Agent trajectories.

The four layers intentionally consume a completed ``RunResult`` and return
immutable score data.  They do not call a provider, ERP gateway, clock, or
filesystem, so the same report can be reproduced in CI and in an exit review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from agent_runtime.agent.contracts import RunResult
from agent_runtime.evaluation.loader import AgentEvaluationCase

LayerName = Literal["component", "trajectory", "task", "system"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class LayerResult:
    layer: LayerName
    checks: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


@dataclass(frozen=True)
class AgentEvaluationReport:
    case_id: str
    component: LayerResult
    trajectory: LayerResult
    task: LayerResult
    system: LayerResult

    @property
    def passed(self) -> bool:
        return all(
            layer.passed for layer in (self.component, self.trajectory, self.task, self.system)
        )


def _actual_tool_sequence(result: RunResult) -> tuple[str, ...]:
    validated_actions: set[tuple[int, str]] = set()
    for event in result.events:
        if event.event_type != "action.validated":
            continue
        step = event.payload.get("step")
        tool_name = event.payload.get("tool_name")
        if isinstance(step, int) and not isinstance(step, bool) and isinstance(tool_name, str):
            validated_actions.add((step, tool_name))

    actual_tools: list[str] = []
    for event in result.events:
        if event.event_type != "action.proposed":
            continue
        step = event.payload.get("step")
        tool_name = event.payload.get("tool_name")
        if (
            isinstance(step, int)
            and not isinstance(step, bool)
            and isinstance(tool_name, str)
            and (step, tool_name) in validated_actions
        ):
            actual_tools.append(tool_name)
    return tuple(actual_tools)


def _observation_count(result: RunResult) -> int:
    return sum(event.event_type in {"tool.observed", "tool.failed"} for event in result.events)


def _check(name: str, passed: bool, detail: str) -> CheckResult:
    return CheckResult(name=name, passed=passed, detail=detail)


def evaluate_component(
    case: AgentEvaluationCase,
    result: RunResult,
) -> LayerResult:
    """Score tool selection and basic component-level output shape."""
    actual_tools = _actual_tool_sequence(result)
    expected_tools = tuple(case.expected.tool_sequence)
    allowed = set(case.allowed_tools)
    return LayerResult(
        layer="component",
        checks=(
            _check(
                "tool_sequence",
                actual_tools == expected_tools,
                f"expected={expected_tools!r}; actual={actual_tools!r}",
            ),
            _check(
                "allowlist",
                set(actual_tools).issubset(allowed),
                f"allowed={tuple(case.allowed_tools)!r}; actual={actual_tools!r}",
            ),
        ),
    )


def evaluate_trajectory(
    case: AgentEvaluationCase,
    result: RunResult,
) -> LayerResult:
    """Score ordering, observations, forbidden calls, and stop reason."""
    actual_tools = _actual_tool_sequence(result)
    observed = _observation_count(result)
    forbidden = set(actual_tools).intersection(case.expected.must_not_call)
    sequences = tuple(event.sequence for event in result.events)
    contiguous = sequences == tuple(range(1, len(sequences) + 1))
    return LayerResult(
        layer="trajectory",
        checks=(
            _check(
                "stop_reason",
                result.stop_reason.code == case.expected.stop_reason,
                f"expected={case.expected.stop_reason!r}; actual={result.stop_reason.code!r}",
            ),
            _check(
                "minimum_observations",
                observed >= case.expected.min_observations,
                f"expected_at_least={case.expected.min_observations}; actual={observed}",
            ),
            _check(
                "must_not_call",
                not forbidden,
                f"forbidden={tuple(case.expected.must_not_call)!r}; actual={actual_tools!r}",
            ),
            _check("trace_sequence", contiguous, f"sequence={sequences!r}"),
        ),
    )


def evaluate_task(
    case: AgentEvaluationCase,
    result: RunResult,
) -> LayerResult:
    """Score the task-level final-answer/evidence shape without business math."""
    final_expected = case.expected.stop_reason == "FINAL_ANSWER"
    final_present = result.final_answer is not None
    evidence_digests = {
        str(event.payload["digest"])
        for event in result.events
        if event.event_type == "tool.observed" and "digest" in event.payload
    }
    evidence_refs = set(result.final_answer.evidence_refs) if result.final_answer else set()
    return LayerResult(
        layer="task",
        checks=(
            _check(
                "final_answer_presence",
                final_present == final_expected,
                f"expected={final_expected}; actual={final_present}",
            ),
            _check(
                "final_answer_evidence",
                not final_present or evidence_refs.issubset(evidence_digests),
                f"refs={tuple(evidence_refs)!r}; observed={tuple(evidence_digests)!r}",
            ),
        ),
    )


def evaluate_system(
    case: AgentEvaluationCase,
    result: RunResult,
) -> LayerResult:
    """Score system-level trace identity, completion, and bounded usage shape."""
    del case
    run_ids = {event.run_id for event in result.events}
    ended = bool(result.events) and result.events[-1].event_type == "run.stopped"
    run_id_is_uuid = all(isinstance(run_id, UUID) for run_id in run_ids)
    usage = result.usage
    bounded_usage = all(
        value >= 0
        for value in (
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.reasoning_tokens,
            usage.cost_microusd,
            result.elapsed_ms,
        )
    )
    return LayerResult(
        layer="system",
        checks=(
            _check(
                "trace_run_identity",
                len(run_ids) <= 1 and run_id_is_uuid,
                "all events share one run id",
            ),
            _check("trace_completion", ended, "trace ends with run.stopped"),
            _check(
                "bounded_usage_shape",
                bounded_usage,
                "usage and elapsed values are non-negative",
            ),
        ),
    )


def evaluate_case(
    case: AgentEvaluationCase,
    result: RunResult,
) -> AgentEvaluationReport:
    """Run all four pure evaluation layers for one golden case."""
    return AgentEvaluationReport(
        case_id=case.case_id,
        component=evaluate_component(case, result),
        trajectory=evaluate_trajectory(case, result),
        task=evaluate_task(case, result),
        system=evaluate_system(case, result),
    )
