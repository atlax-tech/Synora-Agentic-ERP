"""Comparable workflow runners sharing the exact Phase 5 state contract.

These runners are deliberately offline-friendly.  A caller supplies a typed
executor (usually a recorded Gateway adapter in the lab); no runner has access
to Frappe, credentials, arbitrary URLs, or write tools.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from agent_runtime.agent.contracts import Observation
from agent_runtime.workflow.contracts import PlanStep, WorkflowResult, WorkflowState
from agent_runtime.workflow.engine import WorkflowEngine, WorkflowError


class EngineRunner(Protocol):
    async def run(
        self,
        state: WorkflowState,
        execute: Callable[[PlanStep], Awaitable[Observation]],
    ) -> WorkflowResult: ...


class _SequentialRunner:
    """Shared deterministic loop used by fixed, ReAct, and plan modes."""

    label = "handwritten"

    def __init__(self, *, engine: WorkflowEngine | None = None) -> None:
        self.engine = engine or WorkflowEngine()

    async def run(
        self,
        state: WorkflowState,
        execute: Callable[[PlanStep], Awaitable[Observation]],
    ) -> WorkflowResult:
        current = self.engine.start(state) if state.status == "READY" else state
        observations: list[str] = []
        while current.status == "RUNNING":
            ready = self.engine.ready_step(current)
            if ready is None:
                raise WorkflowError("WORKFLOW_INVALID", "running workflow has no ready step")
            current = self.engine.begin_step(current, ready.step_id)
            try:
                observation = await execute(ready)
            except Exception as exc:
                current = self.engine.fail_step(
                    current,
                    code="TOOL_ERROR",
                    detail=str(exc)[:400] or "tool failed",
                )
                return self.engine.result(current, observations=tuple(observations))
            if not observation.ok:
                current = self.engine.fail_step(
                    current,
                    code=observation.error_code or "TOOL_ERROR",
                    detail=observation.summary,
                )
                return self.engine.result(current, observations=tuple(observations))
            observations.append(observation.digest)
            current = self.engine.complete_step(current, observation)
        return self.engine.result(current, observations=tuple(observations))


class FixedWorkflowRunner(_SequentialRunner):
    """Fixed ordered workflow: a deterministic lower-bound comparison."""

    label = "fixed_workflow"


class ReActWorkflowRunner(_SequentialRunner):
    """Bounded ReAct-shaped runner; ready-step selection remains explicit."""

    label = "react_subgraph"


class PlanAndExecuteWorkflowRunner(_SequentialRunner):
    """Handwritten Plan-and-Execute baseline used by the Runtime path."""

    label = "plan_and_execute"


WorkflowEngineProtocol = EngineRunner
