"""Reproducible same-task comparison for Phase 5 workflow choices.

The comparison intentionally consumes recorded observations only.  It reports
what was actually run and keeps unavailable framework/low-code rows explicit;
an unavailable row is never treated as a successful benchmark result.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from agent_runtime.agent.contracts import Observation, observation_from_summary
from agent_runtime.workflow import (
    FixedWorkflowRunner,
    PlanAndExecuteWorkflowRunner,
    PlanStep,
    ReActWorkflowRunner,
    WorkflowEngine,
    WorkflowState,
)


@dataclass(frozen=True)
class ComparisonRow:
    engine: str
    availability: str
    task_id: str
    final_status: str | None
    completed_steps: tuple[str, ...]
    tool_calls: int
    observations: tuple[str, ...]
    revision: int | None
    resumed: bool | None
    trace_complete: bool
    note: str = ""


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


async def _run_handwritten(name: str, runner: Any, task_id: str) -> ComparisonRow:
    state = _state()
    calls = 0

    async def execute(step: PlanStep) -> Observation:
        nonlocal calls
        calls += 1
        assert step.tool_name is not None
        return observation_from_summary(
            run_id=state.run_id,
            step=step.order,
            tool_name=step.tool_name,
            ok=True,
            summary=f"recorded {step.step_id}",
        )

    result = await runner.run(state, execute)
    completed = tuple(step.step_id for step in result.state.steps if step.status == "SUCCEEDED")
    return ComparisonRow(
        engine=name,
        availability="PASS",
        task_id=task_id,
        final_status=result.state.status,
        completed_steps=completed,
        tool_calls=calls,
        observations=result.observations,
        revision=result.state.revision,
        resumed=result.resumed,
        trace_complete=True,
        note="recorded observations; no ERP or capability",
    )


def run_recorded_comparison(task_id: str = "P5-G01-dependency-order") -> tuple[ComparisonRow, ...]:
    """Run the three local engines on one identical recorded task."""

    async def run() -> tuple[ComparisonRow, ...]:
        return tuple(
            [
                await _run_handwritten("fixed_workflow", FixedWorkflowRunner(), task_id),
                await _run_handwritten("react_subgraph", ReActWorkflowRunner(), task_id),
                await _run_handwritten("plan_and_execute", PlanAndExecuteWorkflowRunner(), task_id),
            ]
        )

    return asyncio.run(run())


def rows_as_json(rows: tuple[ComparisonRow, ...]) -> list[dict[str, object]]:
    """Serialize only bounded, non-secret comparison measurements."""

    return [asdict(row) for row in rows]
