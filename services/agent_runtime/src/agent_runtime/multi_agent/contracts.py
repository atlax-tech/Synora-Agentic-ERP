"""Strict contracts for the bounded Phase 9 Planner to Reviewer flow.

The contracts intentionally describe orchestration only.  They contain no
ERP capability, approval state, cookie, prompt, hidden reasoning, or write
operation.  The deterministic plan and the existing enhancement validator
remain the source of truth for facts and final output.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from agent_runtime.agent.contracts import StrictModel, canonical_json
from agent_runtime.agent.safety import check_safe_text

RoleId = Literal[
    "procurement_planner",
    "policy_risk_reviewer",
    "erp_coach",
    "reconciliation_agent",
]
RoleTool = Literal[
    "item.lookup",
    "supplier.lookup",
    "stock.projected",
    "demand.open",
    "material_request.open",
    "purchase_order.open",
]
Decision = Literal["ACCEPT", "REVISE", "REJECT", "ESCALATE"]
IssueCode = Literal[
    "MISSING_FACTS",
    "UNSUPPORTED_CLAIM",
    "DIGEST_MISMATCH",
    "SCOPE_MISMATCH",
    "UNSAFE_ACTION",
    "RISK_CONFLICT",
    "INVALID_SCHEMA",
    "REQUIRES_RECONCILIATION",
    "TIMEOUT",
    "CANCELLED",
    "BUDGET_EXCEEDED",
]
HandoffReason = Literal[
    "INITIAL_REVIEW",
    "REVISION_REQUEST",
    "REVIEW_RESULT",
    "RECONCILIATION",
]
MultiAgentStopCode = Literal[
    "ACCEPTED",
    "REVISED_ACCEPTED",
    "DETERMINISTIC_FALLBACK",
    "REVIEW_REJECTED",
    "REVIEW_ESCALATED",
    "MODEL_ERROR",
    "INVALID_OUTPUT",
    "TIMEOUT",
    "CANCELLED",
    "BUDGET_EXCEEDED",
    "DIGEST_MISMATCH",
    "SCOPE_MISMATCH",
    "LOOP_BLOCKED",
]

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)]
FindingEvidence = Annotated[str, Field(min_length=1, max_length=500)]
MAX_PLAN_PROJECTION_BYTES = 32_000
_ROLE_FIELD_NAMES = frozenset(
    {
        "goal",
        "horizon_days",
        "company",
        "warehouse",
        "summary",
        "findings",
        "generated_at",
    }
)


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _check_safe_text(value: str, *, field_name: str) -> str:
    return check_safe_text(value, field_name=field_name)


class RoleSpec(StrictModel):
    """A role's narrow input and tool boundary."""

    role_id: RoleId
    version: str = Field(pattern=r"^\d+\.\d+$", min_length=3, max_length=20)
    visible_fields: tuple[str, ...] = Field(min_length=1, max_length=16)
    tool_allowlist: tuple[RoleTool, ...] = ()
    output_schema: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,63}$", max_length=64)
    call_budget: int = Field(ge=0, le=3)

    @field_validator("visible_fields")
    @classmethod
    def validate_visible_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(field not in _ROLE_FIELD_NAMES for field in value):
            raise ValueError("visible_fields must be unique deterministic plan fields")
        return value

    @field_validator("tool_allowlist")
    @classmethod
    def validate_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("tool_allowlist must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_role_budget(self) -> RoleSpec:
        if self.role_id == "policy_risk_reviewer" and self.tool_allowlist:
            raise ValueError("policy_risk_reviewer has no tools")
        if self.role_id == "procurement_planner" and self.output_schema != "planner.v1":
            raise ValueError("procurement_planner must use planner.v1")
        if self.role_id == "policy_risk_reviewer" and self.output_schema != "review.v1":
            raise ValueError("policy_risk_reviewer must use review.v1")
        return self


class DeterministicFinding(StrictModel):
    item_code: str = Field(min_length=1, max_length=200)
    risk: str = Field(min_length=1, max_length=80)
    recommendation: str = Field(min_length=1, max_length=4_000)
    evidence: tuple[FindingEvidence, ...] = Field(default=(), max_length=16)
    matched_goal: bool = False

    @field_validator("item_code", "risk", "recommendation", "evidence")
    @classmethod
    def reject_unsafe_values(cls, value: str | tuple[str, ...]) -> str | tuple[str, ...]:
        if isinstance(value, str):
            return _check_safe_text(value, field_name="finding")
        for item in value:
            _check_safe_text(item, field_name="finding evidence")
        return value


class DeterministicPlanView(StrictModel):
    """The only plan projection that a candidate role may receive."""

    goal: str = Field(default="", max_length=4_000)
    horizon_days: int = Field(default=0, ge=0, le=3_650)
    company: str = Field(default="", max_length=200)
    warehouse: str | None = Field(default=None, max_length=200)
    summary: str = Field(min_length=1, max_length=4_000)
    findings: tuple[DeterministicFinding, ...] = Field(default=(), max_length=64)
    generated_at: str = Field(default="", max_length=64)

    @field_validator("goal", "company", "warehouse", "summary", "generated_at")
    @classmethod
    def reject_unsafe_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _check_safe_text(value, field_name="plan projection")


class OrchestrationScope(StrictModel):
    """Trusted caller binding for a candidate orchestration request."""

    task_id: UUID
    run_id: UUID
    correlation_id: UUID
    principal: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    warehouse: str | None = Field(default=None, max_length=200)

    @field_validator("task_id", "run_id", "correlation_id", mode="before")
    @classmethod
    def parse_wire_uuid(cls, value: object) -> UUID:
        if isinstance(value, UUID):
            return value
        if isinstance(value, str):
            try:
                return UUID(value)
            except ValueError as error:
                raise ValueError("scope identity must be a UUID") from error
        raise ValueError("scope identity must be a UUID")

    @field_validator("principal", "company", "warehouse")
    @classmethod
    def reject_unsafe_scope_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _check_safe_text(value, field_name="orchestration scope")


class HandoffEnvelope(StrictModel):
    """A typed, digest-bound handoff between two known roles."""

    task_id: UUID
    run_id: UUID
    correlation_id: UUID
    source_role: RoleId
    target_role: RoleId
    reason: HandoffReason
    expected_result: str = Field(min_length=1, max_length=500)
    shared_state_version: str = Field(pattern=r"^v\d+$", max_length=16)
    shared_state_summary: str = Field(min_length=1, max_length=2_000)
    shared_state_digest: Digest
    depth: int = Field(ge=1, le=2)

    @model_validator(mode="after")
    def validate_handoff(self) -> HandoffEnvelope:
        if self.source_role == self.target_role:
            raise ValueError("handoff source and target must differ")
        transition = (self.source_role, self.target_role, self.reason, self.depth)
        if transition not in {
            ("procurement_planner", "policy_risk_reviewer", "INITIAL_REVIEW", 1),
            ("policy_risk_reviewer", "procurement_planner", "REVISION_REQUEST", 2),
            ("policy_risk_reviewer", "procurement_planner", "REVIEW_RESULT", 2),
            ("policy_risk_reviewer", "reconciliation_agent", "RECONCILIATION", 2),
        }:
            raise ValueError("handoff transition is not allowed")
        if _digest_text(self.shared_state_summary) != self.shared_state_digest:
            raise ValueError("shared_state_digest does not match summary")
        _check_safe_text(self.expected_result, field_name="expected_result")
        _check_safe_text(self.shared_state_summary, field_name="shared_state_summary")
        return self


class PlannerOutput(StrictModel):
    """Candidate explanation only; never an executable ERP instruction."""

    schema_version: Literal["1"] = "1"
    candidate_explanation: str = Field(min_length=1, max_length=4_000)
    citation_summary: tuple[str, ...] = Field(default=(), max_length=32)
    unknowns: tuple[str, ...] = Field(default=(), max_length=32)
    plan_digest: Digest

    @field_validator("candidate_explanation", "citation_summary", "unknowns")
    @classmethod
    def reject_unsafe_text(cls, value: str | tuple[str, ...]) -> str | tuple[str, ...]:
        if isinstance(value, str):
            return _check_safe_text(value, field_name="planner output")
        for item in value:
            _check_safe_text(item, field_name="planner output")
        return value


class ReviewDecision(StrictModel):
    """Fixed review result; it cannot carry facts or authorization."""

    schema_version: Literal["1"] = "1"
    decision: Decision
    issue_codes: tuple[IssueCode, ...] = Field(default=(), max_length=8)
    feedback: str = Field(default="", max_length=500)
    reviewed_plan_digest: Digest

    @model_validator(mode="after")
    def validate_decision(self) -> ReviewDecision:
        if len(set(self.issue_codes)) != len(self.issue_codes):
            raise ValueError("issue_codes must be unique")
        if self.decision == "ACCEPT" and self.issue_codes:
            raise ValueError("ACCEPT cannot include issue codes")
        if self.decision != "ACCEPT" and not self.issue_codes:
            raise ValueError("non-ACCEPT decision requires a fixed issue code")
        _check_safe_text(self.feedback, field_name="review feedback")
        return self


class ReconciliationAdvice(StrictModel):
    """Exception guidance limited to checks and human intervention."""

    schema_version: Literal["1"] = "1"
    suggested_checks: tuple[str, ...] = Field(min_length=1, max_length=8)
    manual_intervention_required: bool = True
    unknowns: tuple[str, ...] = Field(default=(), max_length=16)

    @field_validator("suggested_checks", "unknowns")
    @classmethod
    def reject_actions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _check_safe_text(item, field_name="reconciliation advice")
        return value


class MultiAgentLimits(StrictModel):
    """Hard ceilings for one business chain; lower test limits are allowed."""

    max_model_calls: int = Field(default=3, ge=1, le=3)
    max_revisions: int = Field(default=1, ge=0, le=1)
    max_depth: int = Field(default=2, ge=1, le=2)
    max_concurrency: Literal[1] = 1
    max_wall_time_seconds: int = Field(default=240, ge=1, le=240)


class RoleUsage(StrictModel):
    role_id: RoleId
    calls: int = Field(default=0, ge=0, le=3)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)


class MultiAgentStopReason(StrictModel):
    code: MultiAgentStopCode
    detail: str = Field(default="", max_length=500)
    model_calls: int = Field(ge=0, le=3)
    revision_count: int = Field(ge=0, le=1)
    elapsed_ms: int = Field(ge=0)


class TraceSummary(StrictModel):
    event_count: int = Field(ge=0, le=64)
    event_types: tuple[str, ...] = Field(default=(), max_length=64)
    digest: Digest


class MultiAgentResult(StrictModel):
    """Safe output and only a digest-level trace summary."""

    task_id: UUID
    run_id: UUID
    final_text: str = Field(min_length=1, max_length=4_000)
    stop_reason: MultiAgentStopReason
    role_usage: tuple[RoleUsage, ...] = Field(default=(), max_length=4)
    handoff_count: int = Field(default=0, ge=0, le=4)
    revision_count: int = Field(default=0, ge=0, le=1)
    trace: TraceSummary
    deterministic_validated: bool
    reviewer_decision: ReviewDecision | None = None

    @model_validator(mode="after")
    def validate_result(self) -> MultiAgentResult:
        if self.stop_reason.revision_count != self.revision_count:
            raise ValueError("stop reason revision count mismatch")
        if self.stop_reason.model_calls != sum(item.calls for item in self.role_usage):
            raise ValueError("stop reason model call count mismatch")
        _check_safe_text(self.final_text, field_name="final text")
        return self


def plan_view_from_mapping(plan: object) -> DeterministicPlanView:
    """Project only known deterministic fields and fail closed on malformed values."""
    if not isinstance(plan, Mapping):
        raise ValueError("plan must be an object")
    projected = {key: plan[key] for key in _ROLE_FIELD_NAMES if key in plan}
    raw_findings = projected.get("findings", ())
    if isinstance(raw_findings, (list, tuple)):
        normalized_findings: list[object] = []
        for raw_finding in raw_findings:
            if isinstance(raw_finding, dict):
                finding = dict(raw_finding)
                if isinstance(finding.get("evidence"), list):
                    finding["evidence"] = tuple(finding["evidence"])
                normalized_findings.append(finding)
            else:
                normalized_findings.append(raw_finding)
        projected["findings"] = tuple(normalized_findings)
    try:
        view = DeterministicPlanView.model_validate(projected)
        if (
            len(canonical_json(view.model_dump(mode="json")).encode("utf-8"))
            > MAX_PLAN_PROJECTION_BYTES
        ):
            raise ValueError("deterministic plan projection exceeds size budget")
        return view
    except Exception as error:
        raise ValueError("deterministic plan projection is invalid") from error


def plan_view_digest(view: DeterministicPlanView) -> str:
    return _digest_text(canonical_json(view.model_dump(mode="json")))


def visible_plan_projection(
    view: DeterministicPlanView,
    fields: tuple[str, ...],
) -> dict[str, object]:
    """Return only fields declared by a role's RoleSpec."""
    if any(field not in _ROLE_FIELD_NAMES for field in fields):
        raise ValueError("role requested an unknown plan field")
    values = view.model_dump(mode="json")
    projected = {field: values[field] for field in fields}
    if len(canonical_json(projected).encode("utf-8")) > MAX_PLAN_PROJECTION_BYTES:
        raise ValueError("role plan projection exceeds size budget")
    return projected


def handoff_for(
    *,
    task_id: UUID,
    run_id: UUID,
    correlation_id: UUID,
    source_role: RoleId,
    target_role: RoleId,
    reason: HandoffReason,
    expected_result: str,
    shared_state_summary: str,
    depth: int,
) -> HandoffEnvelope:
    return HandoffEnvelope(
        task_id=task_id,
        run_id=run_id,
        correlation_id=correlation_id,
        source_role=source_role,
        target_role=target_role,
        reason=reason,
        expected_result=expected_result,
        shared_state_version=f"v{depth}",
        shared_state_summary=shared_state_summary,
        shared_state_digest=_digest_text(shared_state_summary),
        depth=depth,
    )


def validate_handoff_identity(
    envelope: HandoffEnvelope,
    *,
    task_id: UUID,
    run_id: UUID,
    correlation_id: UUID,
) -> HandoffEnvelope:
    """Reject a handoff that crosses task, run, or correlation boundaries."""
    if (
        envelope.task_id != task_id
        or envelope.run_id != run_id
        or envelope.correlation_id != correlation_id
    ):
        raise ValueError("handoff identity does not match the current run")
    return envelope


def new_ids() -> tuple[UUID, UUID, UUID]:
    """Create task/run/correlation IDs without persisting business state."""
    return uuid4(), uuid4(), uuid4()
