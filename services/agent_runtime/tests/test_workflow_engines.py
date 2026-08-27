"""Same-task comparison contract for Phase 5 engine runners."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from agent_runtime.agent.contracts import Observation, observation_from_summary
from agent_runtime.evaluation.loader import load_workflow_cases
from agent_runtime.evaluation.workflow_evaluator import evaluate_workflow_case
from agent_runtime.workflow import (
    FixedWorkflowRunner,
    PlanAndExecuteWorkflowRunner,
    PlanStep,
    ReActWorkflowRunner,
    WorkflowEngine,
    WorkflowState,
)


def _state() -> WorkflowState:
    return WorkflowEngine.create_state(
        run_id=uuid4(),
        trace_id=uuid4(),
        steps=(
            PlanStep(
                step_id="material-request",
                order=1,
                type="TOOL",
                allowed_tools=("material_request.open",),
                tool_name="material_request.open",
            ),
            PlanStep(
                step_id="stock",
                order=2,
                type="TOOL",
                depends_on=("material-request",),
                allowed_tools=("stock.projected",),
                tool_name="stock.projected",
            ),
        ),
        deadline=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )


def test_fixed_react_and_plan_runners_share_result_contract() -> None:
    async def execute(step: PlanStep) -> Observation:
        assert step.tool_name is not None
        return observation_from_summary(
            run_id=uuid4(),
            step=step.order,
            tool_name=step.tool_name,
            ok=True,
            summary=f"recorded {step.step_id}",
        )

    async def run() -> None:
        for runner in (
            FixedWorkflowRunner(),
            ReActWorkflowRunner(),
            PlanAndExecuteWorkflowRunner(),
        ):
            result = await runner.run(_state(), execute)
            assert result.state.status == "SUCCEEDED"
            assert result.state.plan_version == 1
            assert len(result.observations) == 2

    asyncio.run(run())


def test_phase5_dataset_has_ten_fixed_cases() -> None:
    cases = load_workflow_cases()
    assert len(cases.cases) == 10
    assert {case.case_id for case in cases.cases} == {
        f"P5-G{index:02d}-{suffix}"
        for index, suffix in (
            (1, "dependency-order"),
            (2, "clarification-resume"),
            (3, "crash-safe-point"),
            (4, "tool-error-replan"),
            (5, "concurrent-resume"),
            (6, "cancel"),
            (7, "expiry"),
            (8, "incompatible-checkpoint"),
            (9, "no-replay"),
            (10, "untrusted-input"),
        )
    }


def test_workflow_evaluator_is_strict_about_status_and_call_count() -> None:
    case = next(
        case for case in load_workflow_cases().cases if case.case_id == "P5-G01-dependency-order"
    )
    state = _state()
    report = evaluate_workflow_case(case, state, tool_calls=2)
    assert not report.passed
    assert {check.name for check in report.checks} == {
        "final_status",
        "completed_steps",
        "tool_calls",
        "interrupt_contract",
    }
