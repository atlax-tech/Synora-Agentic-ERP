"""Strict, engine-neutral contracts for Phase 5 durable workflows.

The workflow checkpoint is an orchestration hint, never an ERP fact or an
authorization record.  Models are frozen so every transition has to produce a
new validated state; this makes completed-step immutability explicit.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BeforeValidator, Field, field_validator, model_validator

from agent_runtime.agent.contracts import (
    JsonValue,
    StrictModel,
    ToolName,
    _validate_json_value,
    canonical_json,
)

WORKFLOW_SCHEMA_VERSION: Literal["1"] = "1"

WorkflowStatus = Literal[
    "READY",
    "RUNNING",
    "INTERRUPTED",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "EXPIRED",
]

StepStatus = Literal[
    "PENDING",
    "READY",
    "RUNNING",
    "WAITING",
    "SUCCEEDED",
    "FAILED",
    "SKIPPED",
    "CANCELLED",
]

ReplanReason = Literal[
    "INPUT_CLARIFIED",
    "TOOL_ERROR",
    "OBSERVATION_CONFLICT",
    "STATE_DRIFT",
    "NO_PROGRESS",
]

StepType = Literal["TOOL", "CLARIFICATION", "FINALIZE"]
AnswerType = Literal["TEXT", "CHOICE", "BOOLEAN", "NUMBER"]


def _tuple_from_wire(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


_STEP_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _check_digest(value: str | None, field_name: str) -> str | None:
    if value is not None and not _DIGEST.fullmatch(value):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return value


def _check_json_mapping(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    _validate_json_value(value)
    canonical_json(value)
    return value


class WorkflowBudget(StrictModel):
    """Small finite budget copied into a checkpoint; no provider secret/cost data."""

    max_steps: int = Field(default=64, ge=1, le=256)
    max_elapsed_ms: int = Field(default=300_000, ge=1_000, le=3_600_000)
    max_observation_bytes: int = Field(default=4_000, ge=128, le=32_000)


class ClarificationRequest(StrictModel):
    """An immutable user-input interrupt with a deliberately narrow schema."""

    schema_version: Literal["1"] = WORKFLOW_SCHEMA_VERSION
    interrupt_id: Annotated[UUID, Field(strict=False)]
    question: str = Field(min_length=1, max_length=1_000)
    answer_type: AnswerType
    answer_max_length: int = Field(default=500, ge=1, le=4_000)
    choices: Annotated[tuple[str, ...], BeforeValidator(_tuple_from_wire)] = Field(
        default=(), max_length=32
    )

    @field_validator("question")
    @classmethod
    def reject_control_text(cls, value: str) -> str:
        if any(ord(character) < 32 and character not in "\n\t" for character in value):
            raise ValueError("clarification question contains control characters")
        return value

    @field_validator("choices")
    @classmethod
    def validate_choices(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("clarification choices must be unique")
        if any(not choice.strip() or len(choice) > 200 for choice in value):
            raise ValueError("clarification choice is invalid")
        return value

    @model_validator(mode="after")
    def validate_answer_shape(self) -> ClarificationRequest:
        if self.answer_type == "CHOICE" and not self.choices:
            raise ValueError("choice clarification requires choices")
        if self.answer_type != "CHOICE" and self.choices:
            raise ValueError("choices are only valid for choice clarification")
        return self


class PlanStep(StrictModel):
    """One deterministic node in a workflow DAG."""

    schema_version: Literal["1"] = WORKFLOW_SCHEMA_VERSION
    step_id: str = Field(min_length=1, max_length=64)
    order: int = Field(ge=1, le=256)
    type: StepType
    depends_on: Annotated[tuple[str, ...], BeforeValidator(_tuple_from_wire)] = Field(
        default=(), max_length=32
    )
    allowed_tools: Annotated[tuple[ToolName, ...], BeforeValidator(_tuple_from_wire)] = Field(
        default=(), max_length=6
    )
    tool_name: ToolName | None = None
    clarification: ClarificationRequest | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    status: StepStatus = "PENDING"
    observation_digest: str | None = None
    error: str | None = Field(default=None, max_length=500)
    completed_at: str | None = Field(default=None, max_length=64)

    @field_validator("step_id")
    @classmethod
    def validate_step_id(cls, value: str) -> str:
        if not _STEP_ID.fullmatch(value):
            raise ValueError("step_id is invalid")
        return value

    @field_validator("depends_on")
    @classmethod
    def validate_dependencies(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(not _STEP_ID.fullmatch(item) for item in value):
            raise ValueError("step dependencies are invalid")
        return value

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _check_json_mapping(value)

    @field_validator("observation_digest")
    @classmethod
    def validate_observation_digest(cls, value: str | None) -> str | None:
        return _check_digest(value, "observation_digest")

    @model_validator(mode="after")
    def validate_tool_shape(self) -> PlanStep:
        if self.type == "TOOL":
            if self.tool_name is None:
                raise ValueError("tool step requires tool_name")
            if self.tool_name not in self.allowed_tools:
                raise ValueError("tool_name must be in allowed_tools")
            if self.clarification is not None:
                raise ValueError("tool step cannot declare clarification")
        elif self.type == "CLARIFICATION":
            if self.clarification is None:
                raise ValueError("clarification step requires clarification")
            if self.tool_name is not None or self.allowed_tools:
                raise ValueError("clarification step cannot declare tools")
        elif self.tool_name is not None or self.allowed_tools:
            raise ValueError("only tool steps may declare tools")
        elif self.clarification is not None:
            raise ValueError("only clarification steps may declare clarification")
        if self.status == "SUCCEEDED" and self.observation_digest is None:
            raise ValueError("succeeded step requires observation_digest")
        if self.status == "SUCCEEDED" and self.completed_at is None:
            raise ValueError("succeeded step requires completed_at")
        if self.status in {"FAILED", "CANCELLED"} and not self.error:
            raise ValueError("failed or cancelled step requires error")
        return self


class WorkflowState(StrictModel):
    """Durable orchestration state, intentionally separate from Frappe Run."""

    schema_version: Literal["1"] = WORKFLOW_SCHEMA_VERSION
    run_id: UUID
    revision: int = Field(default=0, ge=0)
    plan_version: int = Field(default=1, ge=1, le=10_000)
    graph_version: str = Field(default="workflow-v1", min_length=1, max_length=80)
    status: WorkflowStatus = "READY"
    current_step_id: str | None = None
    steps: tuple[PlanStep, ...] = Field(min_length=1, max_length=256)
    clarification: ClarificationRequest | None = None
    replan_reason: ReplanReason | None = None
    budget: WorkflowBudget = WorkflowBudget()
    deadline: str = Field(min_length=1, max_length=64)
    trace_id: UUID
    stop_reason: str | None = Field(default=None, max_length=500)
    crash_recovered: bool = False

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, value: tuple[PlanStep, ...]) -> tuple[PlanStep, ...]:
        validate_plan_dag(value)
        return value

    @field_validator("deadline")
    @classmethod
    def validate_deadline(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("deadline must be an ISO-8601 datetime") from exc
        if parsed.tzinfo is None:
            raise ValueError("deadline must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_state_shape(self) -> WorkflowState:
        if self.status == "INTERRUPTED":
            if self.clarification is None or self.current_step_id is None:
                raise ValueError("interrupted workflow requires clarification and current step")
        elif self.clarification is not None:
            raise ValueError("clarification is only valid while interrupted")
        if self.current_step_id is not None and self.current_step_id not in {
            step.step_id for step in self.steps
        }:
            raise ValueError("current_step_id is unknown")
        if self.status in {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"} and not self.stop_reason:
            raise ValueError("terminal workflow requires stop_reason")
        return self


class WorkflowResult(StrictModel):
    schema_version: Literal["1"] = WORKFLOW_SCHEMA_VERSION
    state: WorkflowState
    observations: tuple[str, ...] = Field(default=(), max_length=256)
    resumed: bool = False


def validate_plan_dag(steps: tuple[PlanStep, ...] | list[PlanStep]) -> None:
    """Reject duplicate IDs, dangling dependencies, cycles, and unstable order."""
    sequence = tuple(steps)
    ids = [step.step_id for step in sequence]
    if len(ids) != len(set(ids)):
        raise ValueError("plan step ids must be unique")
    if [step.order for step in sequence] != list(range(1, len(sequence) + 1)):
        raise ValueError("plan step order must be contiguous and deterministic")
    known = set(ids)
    if any(dependency not in known for step in sequence for dependency in step.depends_on):
        raise ValueError("plan contains a dangling dependency")
    graph = {step.step_id: set(step.depends_on) for step in sequence}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str) -> None:
        if step_id in visiting:
            raise ValueError("plan dependency graph contains a cycle")
        if step_id in visited:
            return
        visiting.add(step_id)
        for dependency in graph[step_id]:
            visit(dependency)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in ids:
        visit(step_id)


def parse_deadline(value: str) -> datetime:
    """Parse a validated deadline for clock comparisons."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("deadline must include a timezone")
    return parsed
