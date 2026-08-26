"""P4.1 four-layer evaluation entry points."""

from uuid import UUID

from agent_runtime.agent.contracts import (
    BudgetSnapshot,
    FinalAnswer,
    JsonValue,
    RunResult,
    StopReason,
    TraceEvent,
    UsageSnapshot,
)
from agent_runtime.evaluation.evaluator import (
    evaluate_case,
    evaluate_component,
    evaluate_system,
    evaluate_task,
    evaluate_trajectory,
)
from agent_runtime.evaluation.loader import load_agent_cases

RUN_ID = UUID("37e1d8a5-1730-4ad0-bffd-217774ed9fab")
DIGEST_ONE = "a" * 64
DIGEST_TWO = "b" * 64


def _event(
    sequence: int,
    event_type: str,
    payload: dict[str, JsonValue] | None = None,
) -> TraceEvent:
    return TraceEvent(
        run_id=RUN_ID,
        sequence=sequence,
        event_type=event_type,  # type: ignore[arg-type]
        timestamp="2026-08-26T00:00:00.000Z",
        payload=payload or {},
    )


def _g01_result() -> RunResult:
    reason = StopReason(
        code="FINAL_ANSWER",
        step=3,
        detail="validated final answer",
        budget_snapshot=BudgetSnapshot(steps=3),
    )
    events = (
        _event(1, "run.started"),
        _event(2, "action.proposed", {"step": 1, "tool_name": "material_request.open"}),
        _event(3, "action.validated", {"step": 1, "tool_name": "material_request.open"}),
        _event(4, "tool.observed", {"tool_name": "material_request.open", "digest": DIGEST_ONE}),
        _event(5, "action.proposed", {"step": 2, "tool_name": "stock.projected"}),
        _event(6, "action.validated", {"step": 2, "tool_name": "stock.projected"}),
        _event(7, "tool.observed", {"tool_name": "stock.projected", "digest": DIGEST_TWO}),
        _event(8, "final.proposed", {"evidence_refs": [DIGEST_TWO]}),
        _event(9, "final.validated"),
        _event(10, "run.stopped", {"code": "FINAL_ANSWER"}),
    )
    return RunResult(
        execution_mode="AGENT",
        final_answer=FinalAnswer(
            status="SUCCEEDED",
            summary="read-only facts collected",
            evidence_refs=(DIGEST_TWO,),
            stop_reason=reason,
        ),
        stop_reason=reason,
        events=events,
        usage=UsageSnapshot(),
    )


def test_four_pure_layers_accept_observation_driven_case() -> None:
    case = next(
        case
        for case in load_agent_cases().cases
        if case.case_id == "P4-G01-observation-driven-second-tool"
    )
    result = _g01_result()

    assert evaluate_component(case, result).passed
    assert evaluate_trajectory(case, result).passed
    assert evaluate_task(case, result).passed
    assert evaluate_system(case, result).passed
    assert evaluate_case(case, result).passed


def test_trajectory_layer_rejects_non_contiguous_trace_sequence() -> None:
    case = next(
        case
        for case in load_agent_cases().cases
        if case.case_id == "P4-G01-observation-driven-second-tool"
    )
    result = _g01_result()
    broken_events = (
        *result.events[:3],
        result.events[3].model_copy(update={"sequence": 99}),
        *result.events[4:],
    )
    broken = result.model_copy(update={"events": broken_events})

    score = evaluate_trajectory(case, broken)

    assert not score.passed
    assert any(check.name == "trace_sequence" and not check.passed for check in score.checks)


def _rejected_action_result(
    *,
    code: str,
    tool_name: str,
    reason: str,
) -> RunResult:
    stop_reason = StopReason(
        code=code,  # type: ignore[arg-type]
        step=1,
        detail=reason,
        budget_snapshot=BudgetSnapshot(steps=1),
    )
    events = (
        _event(1, "run.started"),
        _event(2, "action.proposed", {"step": 1, "tool_name": tool_name}),
        _event(3, "action.rejected", {"step": 1, "reason": reason}),
        _event(4, "run.stopped", {"code": code}),
    )
    return RunResult(
        execution_mode="AGENT",
        final_answer=None,
        stop_reason=stop_reason,
        events=events,
        usage=UsageSnapshot(),
    )


def test_component_layer_excludes_unknown_tool_proposals() -> None:
    case = next(case for case in load_agent_cases().cases if case.case_id == "P4-G03-unknown-tool")

    report = evaluate_case(
        case,
        _rejected_action_result(
            code="TOOL_NOT_ALLOWED",
            tool_name="purchase.submit",
            reason="tool is outside the current allowlist",
        ),
    )

    assert report.component.passed
    assert report.trajectory.passed


def test_component_layer_excludes_invalid_argument_proposals() -> None:
    case = next(case for case in load_agent_cases().cases if case.case_id == "P4-G04-invalid-args")

    report = evaluate_case(
        case,
        _rejected_action_result(
            code="INVALID_TOOL_ARGS",
            tool_name="item.lookup",
            reason="tool arguments failed typed validation",
        ),
    )

    assert report.component.passed
    assert report.trajectory.passed


def test_actual_tool_sequence_ignores_malformed_unvalidated_action_payload() -> None:
    from agent_runtime.evaluation.evaluator import _actual_tool_sequence

    reason = StopReason(
        code="MODEL_ERROR",
        step=1,
        detail="malformed action",
        budget_snapshot=BudgetSnapshot(steps=1),
    )
    result = RunResult(
        execution_mode="AGENT",
        final_answer=None,
        stop_reason=reason,
        events=(
            _event(1, "run.started"),
            _event(2, "action.proposed", {"step": [], "tool_name": {"name": "item.lookup"}}),
            _event(3, "run.stopped", {"code": "MODEL_ERROR"}),
        ),
    )

    assert _actual_tool_sequence(result) == ()
