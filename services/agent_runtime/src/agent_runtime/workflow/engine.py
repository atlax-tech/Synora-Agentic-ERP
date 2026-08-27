"""Handwritten deterministic Plan-and-Execute workflow engine."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from agent_runtime.agent.contracts import Observation
from agent_runtime.workflow.contracts import (
    ClarificationRequest,
    PlanStep,
    ReplanReason,
    WorkflowResult,
    WorkflowState,
    parse_deadline,
    validate_plan_dag,
)


class WorkflowError(Exception):
    """Typed, safe workflow contract error."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class WorkflowToolExecutor(Protocol):
    async def __call__(self, step: PlanStep) -> Observation: ...


@dataclass(frozen=True)
class WorkflowClock:
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


def _now_iso(clock: WorkflowClock) -> str:
    return clock.now().astimezone(UTC).isoformat(timespec="milliseconds")


class WorkflowEngine:
    """One-step-at-a-time executor with no hidden model state or side effects."""

    def __init__(self, *, clock: WorkflowClock | None = None) -> None:
        self._clock = clock or WorkflowClock()

    @staticmethod
    def create_state(
        *,
        run_id: UUID,
        trace_id: UUID | None,
        steps: tuple[PlanStep, ...],
        deadline: str,
        graph_version: str = "workflow-v1",
    ) -> WorkflowState:
        validate_plan_dag(steps)
        return WorkflowState(
            run_id=run_id,
            trace_id=trace_id or uuid4(),
            steps=steps,
            deadline=deadline,
        )

    def ready_step(self, state: WorkflowState) -> PlanStep | None:
        if state.status != "RUNNING":
            return None
        done = {step.step_id for step in state.steps if step.status == "SUCCEEDED"}
        for step in state.steps:
            if step.status in {"PENDING", "READY"} and set(step.depends_on) <= done:
                return step
        return None

    def start(self, state: WorkflowState) -> WorkflowState:
        self._ensure_not_expired(state)
        if state.status != "READY":
            raise WorkflowError("WORKFLOW_CONFLICT", "workflow is not ready")
        ready = self.ready_step(state.model_copy(update={"status": "RUNNING"}))
        if ready is None:
            raise WorkflowError("WORKFLOW_INVALID", "workflow has no executable step")
        steps = self._replace_step(state.steps, ready.step_id, status="READY")
        return state.model_copy(
            update={"status": "RUNNING", "steps": steps, "revision": state.revision + 1}
        )

    def begin_step(self, state: WorkflowState, step_id: str) -> WorkflowState:
        self._ensure_running(state)
        step = self._get_step(state, step_id)
        ready = self.ready_step(state)
        if step.status not in {"PENDING", "READY"} or ready is None or ready.step_id != step_id:
            raise WorkflowError("WORKFLOW_CONFLICT", "step is not ready")
        steps = self._replace_step(state.steps, step_id, status="RUNNING")
        return state.model_copy(
            update={"current_step_id": step_id, "steps": steps, "revision": state.revision + 1}
        )

    def complete_step(self, state: WorkflowState, observation: Observation) -> WorkflowState:
        self._ensure_running(state)
        step_id = state.current_step_id
        if step_id is None:
            raise WorkflowError("WORKFLOW_CONFLICT", "no running step")
        step = self._get_step(state, step_id)
        if step.status != "RUNNING" or step.tool_name != observation.tool_name:
            raise WorkflowError("WORKFLOW_CONFLICT", "observation does not belong to running step")
        updated = step.model_copy(
            update={
                "status": "SUCCEEDED",
                "observation_digest": observation.digest,
                "completed_at": _now_iso(self._clock),
                "error": None,
            }
        )
        steps = tuple(updated if item.step_id == step_id else item for item in state.steps)
        next_step = next(
            (item for item in steps if item.status in {"PENDING", "READY"}),
            None,
        )
        all_done = all(item.status in {"SUCCEEDED", "SKIPPED"} for item in steps)
        if all_done:
            return state.model_copy(
                update={
                    "steps": steps,
                    "current_step_id": None,
                    "status": "SUCCEEDED",
                    "stop_reason": "workflow completed",
                    "revision": state.revision + 1,
                }
            )
        return state.model_copy(
            update={
                "steps": steps,
                "current_step_id": next_step.step_id if next_step else None,
                "revision": state.revision + 1,
            }
        )

    def fail_step(self, state: WorkflowState, *, code: str, detail: str) -> WorkflowState:
        self._ensure_running(state)
        if state.current_step_id is None:
            raise WorkflowError("WORKFLOW_CONFLICT", "no running step")
        step = self._get_step(state, state.current_step_id)
        if step.status != "RUNNING":
            raise WorkflowError("WORKFLOW_CONFLICT", "step is not running")
        updated = step.model_copy(update={"status": "FAILED", "error": f"{code}: {detail}"[:500]})
        steps = tuple(updated if item.step_id == step.step_id else item for item in state.steps)
        return state.model_copy(
            update={
                "steps": steps,
                "status": "FAILED",
                "current_step_id": step.step_id,
                "stop_reason": f"{code}: {detail}"[:500],
                "revision": state.revision + 1,
            }
        )

    def interrupt(self, state: WorkflowState, request: ClarificationRequest) -> WorkflowState:
        self._ensure_running(state)
        if state.current_step_id is None:
            raise WorkflowError("WORKFLOW_CONFLICT", "no running step")
        step = self._get_step(state, state.current_step_id)
        if step.status != "RUNNING":
            raise WorkflowError("WORKFLOW_CONFLICT", "step is not running")
        steps = self._replace_step(state.steps, step.step_id, status="WAITING")
        return state.model_copy(
            update={
                "steps": steps,
                "status": "INTERRUPTED",
                "clarification": request,
                "revision": state.revision + 1,
            }
        )

    def resume(self, state: WorkflowState, *, interrupt_id: UUID, answer: str) -> WorkflowState:
        self._ensure_not_expired(state)
        request = state.clarification
        if state.status != "INTERRUPTED" or request is None:
            raise WorkflowError("WORKFLOW_CONFLICT", "workflow is not waiting for clarification")
        if request.interrupt_id != interrupt_id:
            raise WorkflowError("INTERRUPT_CONFLICT", "interrupt is no longer current")
        if (
            not isinstance(answer, str)
            or not answer.strip()
            or len(answer) > request.answer_max_length
        ):
            raise WorkflowError("INVALID_ANSWER", "clarification answer is invalid")
        if request.answer_type == "CHOICE" and answer not in request.choices:
            raise WorkflowError("INVALID_ANSWER", "clarification answer is not an allowed choice")
        # The answer is intentionally represented only by a bounded marker; raw input
        # must never become a tool parameter without a fresh typed planner decision.
        accepted_digest = hashlib.sha256(b"clarification-answer-accepted").hexdigest()
        steps = self._replace_step(
            state.steps,
            state.current_step_id or "",
            status="SUCCEEDED",
            observation_digest=accepted_digest,
            completed_at=_now_iso(self._clock),
            error=None,
        )
        return state.model_copy(
            update={
                "status": "RUNNING",
                "steps": steps,
                "current_step_id": None,
                "clarification": None,
                "replan_reason": "INPUT_CLARIFIED",
                "revision": state.revision + 1,
            }
        )

    def replan(
        self, state: WorkflowState, steps: tuple[PlanStep, ...], reason: ReplanReason
    ) -> WorkflowState:
        self._ensure_not_expired(state)
        validate_plan_dag(steps)
        completed = {step.step_id: step for step in state.steps if step.status == "SUCCEEDED"}
        for step_id, old_step in completed.items():
            new_step = next((item for item in steps if item.step_id == step_id), None)
            if new_step != old_step:
                raise WorkflowError("PLAN_CONFLICT", "completed steps cannot be modified")
        if any(item.status == "RUNNING" for item in steps):
            raise WorkflowError("PLAN_CONFLICT", "replacement plan cannot contain running steps")
        return state.model_copy(
            update={
                "steps": steps,
                "plan_version": state.plan_version + 1,
                "revision": state.revision + 1,
                "status": "RUNNING",
                "current_step_id": None,
                "clarification": None,
                "replan_reason": reason,
                "stop_reason": None,
            }
        )

    def cancel(self, state: WorkflowState, detail: str = "cancelled") -> WorkflowState:
        if state.status in {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"}:
            raise WorkflowError("WORKFLOW_CONFLICT", "workflow is terminal")
        return state.model_copy(
            update={
                "status": "CANCELLED",
                "stop_reason": detail[:500],
                "revision": state.revision + 1,
                "clarification": None,
            }
        )

    def expire(self, state: WorkflowState) -> WorkflowState:
        if state.status in {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"}:
            return state
        return state.model_copy(
            update={
                "status": "EXPIRED",
                "stop_reason": "workflow deadline expired",
                "revision": state.revision + 1,
                "clarification": None,
            }
        )

    def result(
        self, state: WorkflowState, *, observations: tuple[str, ...] = (), resumed: bool = False
    ) -> WorkflowResult:
        return WorkflowResult(state=state, observations=observations, resumed=resumed)

    def _ensure_running(self, state: WorkflowState) -> None:
        self._ensure_not_expired(state)
        if state.status != "RUNNING":
            raise WorkflowError("WORKFLOW_CONFLICT", "workflow is not running")

    def _ensure_not_expired(self, state: WorkflowState) -> None:
        if self._clock.now() >= parse_deadline(state.deadline):
            raise WorkflowError("WORKFLOW_EXPIRED", "workflow deadline expired")

    @staticmethod
    def _get_step(state: WorkflowState, step_id: str) -> PlanStep:
        for step in state.steps:
            if step.step_id == step_id:
                return step
        raise WorkflowError("WORKFLOW_INVALID", "step is unknown")

    @staticmethod
    def _replace_step(
        steps: tuple[PlanStep, ...], step_id: str, **updates: object
    ) -> tuple[PlanStep, ...]:
        found = False
        result: list[PlanStep] = []
        for step in steps:
            if step.step_id == step_id:
                found = True
                result.append(step.model_copy(update=updates))
            else:
                result.append(step)
        if not found:
            raise WorkflowError("WORKFLOW_INVALID", "step is unknown")
        return tuple(result)
