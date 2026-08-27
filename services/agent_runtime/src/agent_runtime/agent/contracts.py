"""Strict public contracts for the Phase 4 execution kernel.

These models describe explicit model actions, tool observations, final answers,
and auditable trace events.  They deliberately do not contain hidden model
reasoning or any write-capable ERP operation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from agent_runtime.gateway import ToolCall

SCHEMA_VERSION: Literal["1"] = "1"

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None

ToolName = Literal[
    "item.lookup",
    "supplier.lookup",
    "stock.projected",
    "demand.open",
    "material_request.open",
    "purchase_order.open",
]

EvidenceRef = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)]

ExecutionMode = Literal["DETERMINISTIC", "AGENT", "PLAN_EXECUTE"]

StopCode = Literal[
    "FINAL_ANSWER",
    "MAX_STEPS",
    "REPEATED_CALL",
    "NO_PROGRESS",
    "TOKEN_BUDGET",
    "COST_BUDGET",
    "WALL_TIME_BUDGET",
    "CANCELLED",
    "TOOL_NOT_ALLOWED",
    "TOOL_FREQUENCY",
    "INVALID_TOOL_ARGS",
    "TOOL_ERROR",
    "MODEL_ERROR",
    "UNSUPPORTED_FINAL_ANSWER",
]

AgentErrorCategory = Literal[
    "MODEL",
    "TOOL",
    "PERMISSION",
    "SCHEMA",
    "TIMEOUT",
    "BUDGET",
    "CANCEL",
    "INTERNAL",
]

AgentErrorCode = Literal[
    "MODEL_RESPONSE_INVALID",
    "MODEL_PROVIDER_FAILED",
    "TOOL_PERMISSION_DENIED",
    "TOOL_TIMEOUT",
    "TOOL_FAILED",
    "SCHEMA_REJECTED",
    "BUDGET_EXCEEDED",
    "CANCELLED",
    "INTERNAL_ERROR",
]

TraceEventType = Literal[
    "run.started",
    "model.requested",
    "action.proposed",
    "action.validated",
    "action.rejected",
    "tool.started",
    "tool.observed",
    "tool.failed",
    "guard.checked",
    "final.proposed",
    "final.validated",
    "final.rejected",
    "run.stopped",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
        frozen=True,
    )


def _validate_json_value(value: JsonValue) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON numbers must be finite")
    if isinstance(value, list):
        for child in value:
            _validate_json_value(child)
    elif isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            _validate_json_value(child)


def canonical_json(value: object) -> str:
    """Return a stable JSON representation suitable for equality/digest keys."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class Action(StrictModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    step: int = Field(ge=1, le=64)
    tool_name: ToolName
    canonical_args: dict[str, JsonValue] = Field(default_factory=dict)
    correlation_id: UUID

    @field_validator("canonical_args")
    @classmethod
    def validate_args(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _validate_json_value(value)
        # Serializing here also rejects values that cannot be represented on the wire.
        canonical_json(value)
        return value

    def call_key(self) -> str:
        return canonical_json({"tool_name": self.tool_name, "args": self.canonical_args})


class Observation(StrictModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    step: int = Field(ge=1, le=64)
    tool_name: ToolName
    ok: bool
    summary: str = Field(min_length=1, max_length=4_000)
    digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    error_code: str | None = Field(default=None, max_length=80)
    retryable: bool = False

    @model_validator(mode="after")
    def validate_digest(self) -> Observation:
        expected = hashlib.sha256(self.summary.encode("utf-8")).hexdigest()
        if self.digest != expected:
            raise ValueError("observation digest does not match summary")
        return self


class BudgetSnapshot(StrictModel):
    steps: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    cost_microusd: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)


class StopReason(StrictModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    code: StopCode
    step: int = Field(ge=0, le=64)
    detail: str = Field(default="", max_length=500)
    budget_snapshot: BudgetSnapshot


class FinalAnswer(StrictModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    status: Literal["SUCCEEDED", "NEEDS_INPUT", "FAILED"]
    summary: str = Field(min_length=1, max_length=4_000)
    evidence_refs: tuple[EvidenceRef, ...] = Field(default=(), max_length=32)
    unknowns: tuple[str, ...] = Field(default=(), max_length=32)
    stop_reason: StopReason | None = None


class AgentError(StrictModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    category: AgentErrorCategory
    code: AgentErrorCode
    retryable: bool
    safe_message: str = Field(min_length=1, max_length=500)


class TraceEvent(StrictModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    run_id: UUID
    sequence: int = Field(ge=1)
    event_type: TraceEventType
    timestamp: str = Field(min_length=1, max_length=64)
    payload_version: Literal["1"] = SCHEMA_VERSION
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _validate_json_value(value)
        canonical_json(value)
        return value


class UsageSnapshot(StrictModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    cost_microusd: int = Field(default=0, ge=0)


class RunResult(StrictModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    execution_mode: ExecutionMode
    final_answer: FinalAnswer | None = None
    stop_reason: StopReason
    events: tuple[TraceEvent, ...] = ()
    usage: UsageSnapshot = UsageSnapshot()
    elapsed_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_final_answer(self) -> RunResult:
        if self.stop_reason.code == "FINAL_ANSWER" and self.final_answer is None:
            raise ValueError("FINAL_ANSWER requires a final answer")
        return self


_TOOL_ADAPTER: TypeAdapter[ToolCall] = TypeAdapter(ToolCall)
_SENSITIVE_KEY = re.compile(
    r"(?:secret|password|passwd|token|capability|api[_-]?key|authorization|cookie|prompt)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)\b(?:api[_-]?key|bearer|token|secret|password|passwd|capability|authorization|cookie)\b"
    r"\s*[:=]\s*\S+"
)
_MAX_TRACE_DEPTH = 4
_MAX_TRACE_ITEMS = 64
_MAX_TRACE_STRING = 4_000


def validate_action_tool(action: Action) -> ToolCall:
    """Validate an Action against the existing typed read-tool union."""
    return _TOOL_ADAPTER.validate_python(
        {"name": action.tool_name, "version": "1", "input": action.canonical_args}
    )


def observation_from_summary(
    *,
    run_id: UUID,
    step: int,
    tool_name: ToolName,
    ok: bool,
    summary: str,
    error_code: str | None = None,
    retryable: bool = False,
) -> Observation:
    digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()
    return Observation(
        step=step,
        tool_name=tool_name,
        ok=ok,
        summary=summary,
        digest=digest,
        error_code=error_code,
        retryable=retryable,
    )


def _redact(value: JsonValue, secret_values: frozenset[str], *, depth: int = 0) -> JsonValue:
    if depth > _MAX_TRACE_DEPTH:
        return "[TRUNCATED]"
    if isinstance(value, str):
        redacted = value
        for secret in secret_values:
            if secret:
                redacted = redacted.replace(secret, "[REDACTED]")
        return _SENSITIVE_TEXT.sub("[REDACTED]", redacted[:_MAX_TRACE_STRING])
    if isinstance(value, list):
        return [
            _redact(child, secret_values, depth=depth + 1) for child in value[:_MAX_TRACE_ITEMS]
        ]
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if _SENSITIVE_KEY.search(key)
            else _redact(child, secret_values, depth=depth + 1)
            for key, child in list(value.items())[:_MAX_TRACE_ITEMS]
        }
    return value


class TraceRecorder:
    """Build ordered, bounded trace events without accepting secret-bearing fields."""

    def __init__(self, run_id: UUID, *, secret_values: frozenset[str] = frozenset()) -> None:
        self._run_id = run_id
        self._secret_values = secret_values
        self._events: list[TraceEvent] = []

    def add(self, event_type: TraceEventType, payload: dict[str, JsonValue]) -> TraceEvent:
        safe_payload = cast(dict[str, JsonValue], _redact(payload, self._secret_values))
        event = TraceEvent(
            run_id=self._run_id,
            sequence=len(self._events) + 1,
            event_type=event_type,
            timestamp=datetime.now(UTC).isoformat(timespec="milliseconds"),
            payload=safe_payload,
        )
        self._events.append(event)
        return event

    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)
