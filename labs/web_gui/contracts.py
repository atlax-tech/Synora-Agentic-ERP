"""Strict contracts shared by the bounded Phase 11 experiment."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        hide_input_in_errors=True,
    )


ObservationMode = Literal["api", "dom", "aria", "vision", "hybrid"]
DataSource = Literal["synthetic", "erp_readonly"]
TaskStatus = Literal[
    "SUCCEEDED",
    "NOT_FOUND",
    "INCOMPLETE",
    "AUTH_REQUIRED",
    "PERMISSION_DENIED",
    "OBSERVATION_CONFLICT",
    "FAILED",
    "BUDGET_EXCEEDED",
    "STATE_DRIFT",
    "BLOCKED",
]
ActionType = Literal["open", "search", "click", "scroll", "wait", "finish"]


class AllowedField(StrictModel):
    name: Literal["purchase_order", "supplier", "status", "currency"]


class TrialBudget(StrictModel):
    max_actions: int = Field(default=12, ge=1, le=12)
    max_model_calls: int = Field(default=8, ge=0, le=8)
    action_timeout_seconds: float = Field(default=10.0, gt=0, le=10.0)
    wall_time_seconds: float = Field(default=180.0, gt=0, le=180.0)
    max_output_tokens: int = Field(default=1024, ge=1, le=1024)
    max_reobservations: int = Field(default=1, ge=0, le=1)


class TaskSpec(StrictModel):
    case_id: str = Field(min_length=1, max_length=80)
    purchase_order: str = Field(min_length=1, max_length=140)
    allowed_fields: tuple[AllowedField, ...] = (
        AllowedField(name="purchase_order"),
        AllowedField(name="supplier"),
        AllowedField(name="status"),
        AllowedField(name="currency"),
    )
    mode: ObservationMode = "dom"
    data_source: DataSource = "synthetic"
    budget: TrialBudget = TrialBudget()

    @model_validator(mode="after")
    def require_unique_fields(self) -> TaskSpec:
        names = [field.name for field in self.allowed_fields]
        if len(names) != len(set(names)):
            raise ValueError("allowed_fields must be unique")
        return self


class Observation(StrictModel):
    observation_id: UUID = Field(default_factory=uuid4)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    page_version: str = Field(min_length=1, max_length=120)
    source: DataSource
    mode: ObservationMode
    content: str = Field(default="", max_length=50_000)
    screenshot_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    viewport_width: int | None = Field(default=None, ge=1, le=10_000)
    viewport_height: int | None = Field(default=None, ge=1, le=10_000)


class ActionProposal(StrictModel):
    action_id: UUID = Field(default_factory=uuid4)
    action_type: ActionType
    observation_id: UUID
    target_ref: str | None = Field(default=None, min_length=1, max_length=120)
    text: str | None = Field(default=None, min_length=1, max_length=140)
    x: float | None = Field(default=None, ge=0)
    y: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_target_shape(self) -> ActionProposal:
        coordinate_click = self.action_type == "click" and self.x is not None and self.y is not None
        if self.action_type in {"click", "search"} and not (
            self.target_ref or self.text or coordinate_click
        ):
            raise ValueError("click and search actions require an observed target")
        if self.action_type == "click" and self.x is not None and self.y is None:
            raise ValueError("coordinate clicks require both x and y")
        if self.action_type == "click" and self.y is not None and self.x is None:
            raise ValueError("coordinate clicks require both x and y")
        if self.action_type != "click" and (self.x is not None or self.y is not None):
            raise ValueError("coordinates are only valid for click actions")
        return self


class ActionReceipt(StrictModel):
    action_id: UUID
    observation_id: UUID
    result: Literal["APPLIED", "REJECTED", "FAILED"]
    error_code: str | None = Field(default=None, max_length=80)
    before_observation_id: UUID
    after_observation_id: UUID | None = None
    stop_reason: str | None = Field(default=None, max_length=160)


class TaskResult(StrictModel):
    case_id: str = Field(min_length=1, max_length=80)
    status: TaskStatus
    fields: dict[str, str | None] = Field(default_factory=dict)
    evidence_refs: tuple[UUID, ...] = ()
    observation_complete: bool = False
    actions: tuple[ActionReceipt, ...] = ()
    stop_reason: str | None = Field(default=None, max_length=160)


class TrialResult(StrictModel):
    case_id: str = Field(min_length=1, max_length=80)
    method: ObservationMode
    data_source: DataSource
    status: TaskStatus
    task_correct: bool
    safety_pass: bool
    latency_ms: int = Field(ge=0)
    model_calls: int = Field(ge=0, le=8)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    failure_code: str | None = Field(default=None, max_length=80)
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
