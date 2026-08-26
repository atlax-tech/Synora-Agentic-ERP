"""Small, reproducible comparison records for the Phase 4 lab patterns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agent_runtime.agent.contracts import RunResult, StopCode
from agent_runtime.evaluation.evaluator import AgentEvaluationReport, evaluate_case
from agent_runtime.evaluation.loader import AgentEvaluationCase


@dataclass(frozen=True)
class PatternMetrics:
    """Comparable, non-secret measurements for one pattern and one case."""

    pattern: str
    case_id: str
    success: bool
    trajectory_correct: bool
    stop_reason: StopCode
    tool_calls: int
    observations: int
    elapsed_ms: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cost_microusd: int
    trace_events: int
    complexity: str = "bounded O(steps)"


def _tool_calls(result: RunResult) -> int:
    return sum(event.event_type == "tool.started" for event in result.events)


def _observations(result: RunResult) -> int:
    return sum(event.event_type in {"tool.observed", "tool.failed"} for event in result.events)


def summarize_run(
    pattern: str,
    case: AgentEvaluationCase,
    result: RunResult,
    report: AgentEvaluationReport | None = None,
) -> PatternMetrics:
    """Convert one completed run into the common comparison shape."""
    evaluation = report or evaluate_case(case, result)
    usage = result.usage
    return PatternMetrics(
        pattern=pattern,
        case_id=case.case_id,
        success=evaluation.passed,
        trajectory_correct=evaluation.trajectory.passed,
        stop_reason=result.stop_reason.code,
        tool_calls=_tool_calls(result),
        observations=_observations(result),
        elapsed_ms=result.elapsed_ms,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cost_microusd=usage.cost_microusd,
        trace_events=len(result.events),
    )


def compare_case(
    case: AgentEvaluationCase,
    runs: Mapping[str, RunResult],
) -> tuple[PatternMetrics, ...]:
    """Summarize several patterns while preserving caller-provided order."""
    return tuple(summarize_run(pattern, case, result) for pattern, result in runs.items())
