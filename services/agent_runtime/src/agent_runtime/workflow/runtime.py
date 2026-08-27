"""Runtime-facing durable workflow service.

This module owns orchestration only.  Every ERP read still travels through the
existing capability-authenticated typed Gateway adapter; the SQLite store never
receives that capability.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BeforeValidator, ConfigDict, Field, SecretStr, field_validator

from agent_runtime.agent.contracts import Action, StrictModel
from agent_runtime.agent.execution import GatewayToolAdapter
from agent_runtime.gateway import GatewayClient
from agent_runtime.workflow.checkpoint import (
    CheckpointConflict,
    CheckpointError,
    SQLiteCheckpointStore,
)
from agent_runtime.workflow.contracts import (
    ClarificationRequest,
    PlanStep,
    WorkflowResult,
    WorkflowState,
)
from agent_runtime.workflow.engine import WorkflowEngine, WorkflowError

WORKFLOW_RUNTIME_SCHEMA_VERSION: Literal["1"] = "1"


def _tuple_from_wire(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


class WorkflowRequest(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    schema_version: Literal["1"] = WORKFLOW_RUNTIME_SCHEMA_VERSION
    run_id: Annotated[UUID, Field(strict=False)]
    correlation_id: Annotated[UUID, Field(strict=False)]
    capability: SecretStr = Field(repr=False)

    @field_validator("capability")
    @classmethod
    def validate_capability(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if len(token) != 43 or any(
            not (character.isascii() and (character.isalnum() or character in "_-"))
            for character in token
        ):
            raise ValueError("capability is invalid")
        return value


class WorkflowStartRequest(WorkflowRequest):
    goal: str = Field(min_length=1, max_length=1_000)
    deadline: str | None = Field(default=None, max_length=64)
    steps: Annotated[tuple[PlanStep, ...], BeforeValidator(_tuple_from_wire)] | None = Field(
        default=None, max_length=256
    )


class WorkflowResumeRequest(WorkflowRequest):
    workflow_revision: int = Field(ge=0, le=1_000_000)
    interrupt_id: Annotated[UUID, Field(strict=False)]
    answer: str = Field(min_length=1, max_length=4_000)


class WorkflowCancelRequest(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    schema_version: Literal["1"] = WORKFLOW_RUNTIME_SCHEMA_VERSION
    run_id: Annotated[UUID, Field(strict=False)]
    workflow_revision: int = Field(ge=0, le=1_000_000)


class WorkflowStatusRequest(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    schema_version: Literal["1"] = WORKFLOW_RUNTIME_SCHEMA_VERSION
    run_id: Annotated[UUID, Field(strict=False)]


class WorkflowResponse(StrictModel):
    schema_version: Literal["1"] = WORKFLOW_RUNTIME_SCHEMA_VERSION
    result: WorkflowResult


def _default_deadline() -> str:
    return (datetime.now(UTC) + timedelta(hours=24)).isoformat(timespec="milliseconds")


def default_plan(goal: str, run_id: UUID) -> tuple[PlanStep, ...]:
    """Create a bounded read-only plan when Frappe did not provide one.

    A goal containing an explicit clarification cue gets a deterministic input
    node first; no user text is copied into tool parameters.
    """
    steps: list[PlanStep] = []
    normalized = goal.casefold()
    if any(marker in normalized for marker in ("clarif", "which warehouse", "需要确认")):
        request = ClarificationRequest(
            interrupt_id=uuid5(NAMESPACE_URL, f"synora:workflow:{run_id}:warehouse"),
            question="请选择本次只读分析要使用的仓库。",
            answer_type="TEXT",
            answer_max_length=140,
        )
        steps.append(
            PlanStep(
                step_id="warehouse-clarification",
                order=1,
                type="CLARIFICATION",
                clarification=request,
            )
        )
    order = len(steps) + 1
    dependencies = (steps[-1].step_id,) if steps else ()
    steps.append(
        PlanStep(
            step_id="open-material-requests",
            order=order,
            type="TOOL",
            depends_on=dependencies,
            allowed_tools=("material_request.open",),
            tool_name="material_request.open",
            parameters={"offset": 0, "limit": 20},
        )
    )
    return tuple(steps)


class WorkflowRuntime:
    """Execute one workflow segment and persist every safe point."""

    def __init__(self, store: SQLiteCheckpointStore | None = None) -> None:
        self.store = store or SQLiteCheckpointStore.from_environment()
        self.engine = WorkflowEngine()

    async def start(self, request: WorkflowStartRequest) -> WorkflowResponse:
        steps = request.steps or default_plan(request.goal, request.run_id)
        initial = WorkflowEngine.create_state(
            run_id=request.run_id,
            trace_id=request.correlation_id,
            steps=steps,
            deadline=request.deadline or _default_deadline(),
        )
        try:
            self.store.create(initial)
            state = initial
        except CheckpointConflict:
            state = self.store.load(request.run_id)
        return WorkflowResponse(result=await self._advance(state, request))

    async def resume(self, request: WorkflowResumeRequest) -> WorkflowResponse:
        state = self.store.load(request.run_id)
        if state.revision != request.workflow_revision:
            raise CheckpointConflict("workflow revision is stale")
        lease = self.store.acquire_lease(request.run_id, expected_revision=state.revision)
        try:
            resumed = self.engine.resume(
                state,
                interrupt_id=request.interrupt_id,
                answer=request.answer,
            )
            self.store.save(
                resumed,
                expected_revision=state.revision,
                lease_id=lease,
                keep_lease=True,
            )
        except Exception:
            self.store.release_lease(request.run_id, lease)
            raise
        return WorkflowResponse(result=await self._advance(resumed, request, lease=lease))

    def status(self, request: WorkflowStatusRequest) -> WorkflowResponse:
        return WorkflowResponse(result=WorkflowResult(state=self.store.load(request.run_id)))

    def cancel(self, request: WorkflowCancelRequest) -> WorkflowResponse:
        state = self.store.load(request.run_id)
        if state.revision != request.workflow_revision:
            raise CheckpointConflict("workflow revision is stale")
        lease = self.store.acquire_lease(request.run_id, expected_revision=state.revision)
        try:
            cancelled = self.engine.cancel(state)
            self.store.save(cancelled, expected_revision=state.revision, lease_id=lease)
        except Exception:
            self.store.release_lease(request.run_id, lease)
            raise
        return WorkflowResponse(result=WorkflowResult(state=cancelled))

    async def recover(self) -> int:
        """Mark in-flight tool calls as an explicit manual-recovery interrupt."""
        recovered = 0
        for state in self.store.recoverable():
            if state.status != "RUNNING" or state.current_step_id is None:
                continue
            step = next(item for item in state.steps if item.step_id == state.current_step_id)
            if step.status != "RUNNING":
                continue
            lease = self.store.acquire_lease(state.run_id, expected_revision=state.revision)
            request = ClarificationRequest(
                interrupt_id=uuid5(
                    NAMESPACE_URL, f"synora:workflow:{state.run_id}:crash:{state.revision}"
                ),
                question="Runtime 在只读工具调用期间重启, 请选择是否创建新的只读重试。",
                answer_type="CHOICE",
                choices=("retry", "inspect"),
                answer_max_length=20,
            )
            try:
                interrupted = self.engine.interrupt(state, request)
                interrupted = interrupted.model_copy(
                    update={"crash_recovered": True, "replan_reason": "STATE_DRIFT"}
                )
                self.store.save(interrupted, expected_revision=state.revision, lease_id=lease)
            except Exception:
                self.store.release_lease(state.run_id, lease)
                raise
            recovered += 1
        return recovered

    async def _advance(
        self,
        state: WorkflowState,
        request: WorkflowRequest,
        *,
        lease: str | None = None,
    ) -> WorkflowResult:
        current = state
        held_lease = lease
        client: GatewayClient | None = None
        adapter: GatewayToolAdapter | None = None
        try:
            if current.status == "READY":
                held_lease = held_lease or self.store.acquire_lease(
                    current.run_id, expected_revision=current.revision
                )
                started = self.engine.start(current)
                self.store.save(
                    started,
                    expected_revision=current.revision,
                    lease_id=held_lease,
                    keep_lease=True,
                )
                current = started
            if current.status in {"INTERRUPTED", "SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"}:
                return self.engine.result(current, resumed=state is not current)
            if current.current_step_id is not None:
                active = next(
                    item for item in current.steps if item.step_id == current.current_step_id
                )
                if active.status == "RUNNING":
                    raise WorkflowError(
                        "UNCERTAIN_TOOL_RESULT",
                        "a running tool has no durable result; manual recovery is required",
                    )
            client = GatewayClient()
            adapter = GatewayToolAdapter(
                client=client,
                run_id=request.run_id,
                correlation_id=request.correlation_id,
                capability=request.capability,
            )
            while current.status == "RUNNING":
                ready = self.engine.ready_step(current)
                if ready is None:
                    raise WorkflowError("WORKFLOW_INVALID", "running workflow has no ready step")
                held_lease = held_lease or self.store.acquire_lease(
                    current.run_id, expected_revision=current.revision
                )
                started = self.engine.begin_step(current, ready.step_id)
                self.store.save(
                    started,
                    expected_revision=current.revision,
                    lease_id=held_lease,
                    keep_lease=True,
                )
                current = started
                if ready.type == "CLARIFICATION":
                    if ready.clarification is None:
                        raise WorkflowError("WORKFLOW_INVALID", "clarification step is incomplete")
                    interrupted = self.engine.interrupt(current, ready.clarification)
                    self.store.save(
                        interrupted,
                        expected_revision=current.revision,
                        lease_id=held_lease,
                    )
                    return self.engine.result(interrupted)
                if ready.tool_name is None:
                    raise WorkflowError("WORKFLOW_INVALID", "tool step is incomplete")
                observation = await adapter.execute(
                    Action(
                        step=ready.order,
                        tool_name=ready.tool_name,
                        canonical_args=ready.parameters,
                        correlation_id=request.correlation_id,
                    )
                )
                completed = self.engine.complete_step(current, observation)
                self.store.save(
                    completed,
                    expected_revision=current.revision,
                    lease_id=held_lease,
                )
                current = completed
                held_lease = None
            return self.engine.result(current, resumed=state is not current)
        except CheckpointError:
            raise
        except WorkflowError:
            if held_lease:
                self.store.release_lease(current.run_id, held_lease)
            raise
        except Exception as exc:
            if held_lease:
                self.store.release_lease(current.run_id, held_lease)
            raise WorkflowError(
                "TOOL_ERROR", str(exc)[:400] or "workflow execution failed"
            ) from exc
        finally:
            if adapter is not None:
                await adapter.aclose()
            elif client is not None:
                await client.aclose()
