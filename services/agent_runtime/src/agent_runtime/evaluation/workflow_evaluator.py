"""Pure comparison checks for the Phase 5 same-task workflow dataset."""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.evaluation.loader import WorkflowEvaluationCase
from agent_runtime.workflow.contracts import WorkflowState


@dataclass(frozen=True)
class WorkflowCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class WorkflowEvaluationReport:
    case_id: str
    checks: tuple[WorkflowCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


def evaluate_workflow_case(
    case: WorkflowEvaluationCase,
    state: WorkflowState,
    *,
    tool_calls: int,
) -> WorkflowEvaluationReport:
    expected = case.expected
    actual_completed = tuple(step.step_id for step in state.steps if step.status == "SUCCEEDED")
    checks = (
        WorkflowCheck(
            "final_status",
            state.status == expected.final_status,
            f"expected={expected.final_status}; actual={state.status}",
        ),
        WorkflowCheck(
            "completed_steps",
            actual_completed == expected.completed_steps,
            f"expected={expected.completed_steps!r}; actual={actual_completed!r}",
        ),
        WorkflowCheck(
            "tool_calls",
            tool_calls == expected.expected_tool_calls,
            f"expected={expected.expected_tool_calls}; actual={tool_calls}",
        ),
        WorkflowCheck(
            "interrupt_contract",
            (state.status == "INTERRUPTED") == expected.requires_interrupt,
            f"expected={expected.requires_interrupt}; actual={state.status == 'INTERRUPTED'}",
        ),
    )
    return WorkflowEvaluationReport(case_id=case.case_id, checks=checks)
