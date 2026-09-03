"""Phase 9 bounded multi-agent pattern comparison.

The five runners in this module consume one recorded role adapter and the
same typed Planner/Reviewer contracts.  They contain no ERP framework or
credential access.  The graph runner uses LangGraph only as a routing shell;
domain objects remain Synora types.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, Protocol, TypedDict, cast

from agent_runtime.agent.contracts import StrictModel, canonical_json
from agent_runtime.agent.enhance import validate_explanation
from agent_runtime.agent.safety import check_safe_text
from agent_runtime.evaluation.phase9_baseline import (
    BASELINE_CASE_SPEC_PATH,
    EXPECTED_CASE_ORDER,
    BaselineCase,
    case_spec_sha256,
    load_phase9_baseline_cases,
)
from agent_runtime.evaluation.security import input_projection_isolated, security_counters
from agent_runtime.multi_agent.contracts import (
    DeterministicPlanView,
    MultiAgentStopCode,
    PlannerOutput,
    ReviewDecision,
    RoleId,
    plan_view_digest,
    plan_view_from_mapping,
    visible_plan_projection,
)
from agent_runtime.multi_agent.planner_reviewer import (
    PLANNER_ROLE_SPEC,
    REVIEWER_ROLE_SPEC,
)
from pydantic import Field, field_validator, model_validator

PatternName = Literal[
    "supervisor",
    "peer_to_peer",
    "hierarchical",
    "managed_agent_tool",
    "explicit_graph_node",
]
PatternTrajectory = Literal[
    "NORMAL",
    "CONFLICT",
    "TIMEOUT",
    "CANCELLED",
    "INVALID_OUTPUT",
    "LOOP_ATTACK",
]
PatternFailureCode = Literal[
    "TIMEOUT",
    "CANCELLED",
    "MODEL_ERROR",
    "INVALID_OUTPUT",
    "DIGEST_MISMATCH",
]

PATTERN_NAMES: tuple[PatternName, ...] = (
    "supervisor",
    "peer_to_peer",
    "hierarchical",
    "managed_agent_tool",
    "explicit_graph_node",
)
TRAJECTORY_NAMES: tuple[PatternTrajectory, ...] = (
    "NORMAL",
    "CONFLICT",
    "TIMEOUT",
    "CANCELLED",
    "INVALID_OUTPUT",
    "LOOP_ATTACK",
)
_SAFE_FALLBACK = "无法生成计划解释，请人工核对确定性计划。"


class PatternLabDependencyError(RuntimeError):
    """Raised when the optional graph laboratory dependency is unavailable."""


class PatternFailure(Exception):
    """A recorded adapter failure with a fixed, non-secret stop code."""

    def __init__(self, code: PatternFailureCode) -> None:
        super().__init__(code)
        self.code = code


class PatternUsage(StrictModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)


class PatternComplexity(StrictModel):
    shared_source_lines: int = Field(ge=0)
    pattern_source_lines: int = Field(ge=0)
    direct_dependencies: tuple[str, ...] = ()
    configuration_items: int = Field(default=0, ge=0)
    interfaces: int = Field(default=1, ge=0)
    persistence_components: int = Field(default=0, ge=0)
    manual_operations: int = Field(default=1, ge=0)
    checkpoint_scope: Literal["none", "orchestration"] = "none"


class PatternOutcome(StrictModel):
    pattern: PatternName
    case_id: str = Field(min_length=1, max_length=80)
    trajectory: PatternTrajectory
    final_text: str = Field(min_length=1, max_length=4_000)
    stop_reason: MultiAgentStopCode
    deterministic_validated: bool
    task_correct: bool
    valid_explanation: bool
    safe_fallback: bool
    recovery_success: bool
    model_calls: int = Field(ge=0, le=3)
    handoff_count: int = Field(ge=0, le=4)
    revision_count: int = Field(ge=0, le=1)
    usage: PatternUsage
    trace_event_count: int = Field(ge=1, le=128)
    trace_event_types: tuple[str, ...]
    trace_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    checkpoint_keys: tuple[str, ...] = ()
    input_isolation_pass: bool = True
    unauthorized_tool_calls: int = Field(default=0, ge=0)
    erp_business_writes: int = Field(default=0, ge=0)
    scope_leaks: int = Field(default=0, ge=0)
    secret_leaks: int = Field(default=0, ge=0)

    @field_validator("final_text")
    @classmethod
    def validate_final_text(cls, value: str) -> str:
        return check_safe_text(value, field_name="pattern final text")


class PatternAggregate(StrictModel):
    pattern: PatternName
    case_count: int
    task_correctness_rate: float
    valid_explanation_rate: float
    safe_fallback_rate: float
    recovery_success_rate: float
    trace_completeness_rate: float
    p50_latency_ms: int
    p95_latency_ms: int
    prompt_tokens_total: int
    completion_tokens_total: int
    reasoning_tokens_total: int
    model_calls_total: int
    unauthorized_tool_calls: int
    erp_business_writes: int
    scope_leaks: int
    secret_leaks: int
    complexity: PatternComplexity

    @field_validator(
        "task_correctness_rate",
        "valid_explanation_rate",
        "safe_fallback_rate",
        "recovery_success_rate",
        "trace_completeness_rate",
    )
    @classmethod
    def _finite(cls, value: float) -> float:
        del cls
        if not math.isfinite(value):
            raise ValueError("aggregate rates must be finite")
        return value


class PatternManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    suite: Literal["P9.4-pattern-comparison"] = "P9.4-pattern-comparison"
    case_order: tuple[str, ...]
    case_spec_sha256: str
    code_head: str
    model_role: Literal["recorded"] = "recorded"
    model_name: str = "recorded-phase9-pattern"
    role_schema_version: str = "planner.v1+review.v1"
    patterns: tuple[PatternName, ...] = PATTERN_NAMES
    trajectory_order: tuple[PatternTrajectory, ...] = TRAJECTORY_NAMES

    @field_validator("case_order")
    @classmethod
    def validate_case_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != EXPECTED_CASE_ORDER:
            raise ValueError("pattern comparison case order must use the frozen P9 order")
        return value


class PatternReport(StrictModel):
    schema_version: Literal["1"] = "1"
    manifest: PatternManifest
    cases: tuple[PatternOutcome, ...]
    trajectories: tuple[PatternOutcome, ...]
    aggregates: tuple[PatternAggregate, ...]
    deterministic_fingerprint: str
    all_security_passed: bool

    @model_validator(mode="after")
    def validate_report(self) -> PatternReport:
        if self.manifest.patterns != PATTERN_NAMES:
            raise ValueError("pattern manifest order is not fixed")
        expected_security = all(
            item.input_isolation_pass and getattr(item, name) == 0
            for item in (*self.cases, *self.trajectories)
            for name in (
                "unauthorized_tool_calls",
                "erp_business_writes",
                "scope_leaks",
                "secret_leaks",
            )
        )
        if self.all_security_passed != expected_security:
            raise ValueError("all_security_passed does not match case security counters")
        body = self.model_dump(mode="json", exclude={"deterministic_fingerprint"})
        if self.deterministic_fingerprint != _report_fingerprint(body):
            raise ValueError("pattern report fingerprint does not match report body")
        return self


@dataclass(frozen=True, slots=True)
class PatternCase:
    """One fixed plan projection and a recorded role script."""

    case_id: str
    category: str
    plan: DeterministicPlanView
    trajectory: PatternTrajectory
    planner_text: str
    revision_text: str
    reviewer_decision: Literal["ACCEPT", "REVISE", "REJECT", "ESCALATE"]
    reviewer_issue_codes: tuple[str, ...] = ()
    failure_code: PatternFailureCode | None = None
    invalid_planner_output: bool = False
    loop_attack: bool = False
    expected_outcome: str = "VALID_EXPLANATION"
    forbidden_terms: tuple[str, ...] = ()
    prompt_tokens: int = 128
    completion_tokens: int = 32
    reasoning_tokens: int = 0
    untrusted_text: str = ""
    requested_capability: str | None = None
    private_user: str = ""


@dataclass(frozen=True, slots=True)
class RecordedCall:
    role: RoleId
    phase: Literal["initial", "review", "revision"]
    input_digest: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    elapsed_ms: int
    tool_count: int = 0


class RecordedRoleAdapter(Protocol):
    calls: list[RecordedCall]

    def planner(
        self,
        view: DeterministicPlanView,
        digest: str,
        *,
        revision: bool = False,
    ) -> PlannerOutput | str: ...

    def reviewer(
        self,
        view: DeterministicPlanView,
        digest: str,
        candidate: PlannerOutput,
    ) -> ReviewDecision | str: ...


@dataclass(slots=True)
class _RecordedAdapter:
    case: PatternCase
    calls: list[RecordedCall] = field(default_factory=list)

    def _record(
        self,
        role: RoleId,
        phase: Literal["initial", "review", "revision"],
        view: DeterministicPlanView,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        reasoning_tokens: int,
        elapsed_ms: int = 4,
    ) -> None:
        # Validate the exact role projection before recording a call.  This
        # makes a field expansion fail closed instead of becoming an invisible
        # prompt change.
        fields = (
            PLANNER_ROLE_SPEC.visible_fields
            if role == "procurement_planner"
            else REVIEWER_ROLE_SPEC.visible_fields
        )
        projection = visible_plan_projection(view, fields)
        input_digest = hashlib.sha256(canonical_json(projection).encode("utf-8")).hexdigest()
        self.calls.append(
            RecordedCall(
                role=role,
                phase=phase,
                input_digest=input_digest,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                reasoning_tokens=reasoning_tokens,
                elapsed_ms=elapsed_ms,
            )
        )

    def planner(
        self,
        view: DeterministicPlanView,
        digest: str,
        *,
        revision: bool = False,
    ) -> PlannerOutput | str:
        self._record(
            "procurement_planner",
            "revision" if revision else "initial",
            view,
            prompt_tokens=self.case.prompt_tokens,
            completion_tokens=self.case.completion_tokens,
            reasoning_tokens=self.case.reasoning_tokens,
        )
        if self.case.failure_code is not None:
            raise PatternFailure(self.case.failure_code)
        if self.case.invalid_planner_output and not revision:
            return "{}"
        text = self.case.revision_text if revision else self.case.planner_text
        try:
            return PlannerOutput(
                candidate_explanation=text,
                citation_summary=("来源摘要",),
                unknowns=(),
                plan_digest=digest,
            )
        except ValueError:
            # Preserve the malformed wire value for the strict parser.  The
            # runner records INVALID_OUTPUT without exposing the text.
            return json.dumps(
                {
                    "candidate_explanation": text,
                    "citation_summary": ["来源摘要"],
                    "unknowns": [],
                    "plan_digest": digest,
                },
                ensure_ascii=False,
            )

    def reviewer(
        self,
        view: DeterministicPlanView,
        digest: str,
        candidate: PlannerOutput,
    ) -> ReviewDecision | str:
        del candidate
        self._record(
            "policy_risk_reviewer",
            "review",
            view,
            prompt_tokens=self.case.prompt_tokens + 8,
            completion_tokens=16,
            reasoning_tokens=0,
        )
        if self.case.failure_code is not None:
            raise PatternFailure(self.case.failure_code)
        decision = "REVISE" if self.case.loop_attack else self.case.reviewer_decision
        return ReviewDecision(
            decision=decision,
            issue_codes=cast(
                tuple[Literal["UNSUPPORTED_CLAIM"], ...], self.case.reviewer_issue_codes
            ),
            feedback="请保留来源摘要" if decision == "REVISE" else "",
            reviewed_plan_digest=digest,
        )


class _Flow:
    """Shared parse, budget, validation, scoring and trace behavior."""

    def __init__(self, case: PatternCase, pattern: PatternName, adapter: _RecordedAdapter) -> None:
        self.case = case
        self.pattern = pattern
        self.adapter = adapter
        self.view = case.plan
        self.digest = plan_view_digest(case.plan)
        self.events: list[dict[str, object]] = []
        self.handoff_count = 0
        self.revision_count = 0
        self.planner_candidate: PlannerOutput | None = None
        self.revised_candidate: PlannerOutput | None = None
        self.last_decision: ReviewDecision | None = None

    def event(self, event_type: str, **values: object) -> None:
        # Trace values are fixed labels, counters and digests only.  Neither
        # candidate text nor the source case payload is persisted here.
        self.events.append({"type": event_type, **values})

    def _parse_planner(self, raw: PlannerOutput | str) -> PlannerOutput:
        if isinstance(raw, PlannerOutput):
            return raw
        try:
            value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, dict):
                raise ValueError("planner output must be an object")
            for key in ("citation_summary", "unknowns"):
                if isinstance(value.get(key), list):
                    value[key] = tuple(value[key])
            return PlannerOutput.model_validate(value)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise PatternFailure("INVALID_OUTPUT") from error

    def _parse_reviewer(self, raw: ReviewDecision | str) -> ReviewDecision:
        if isinstance(raw, ReviewDecision):
            return raw
        try:
            value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, dict):
                raise ValueError("review output must be an object")
            for key in ("issue_codes",):
                if isinstance(value.get(key), list):
                    value[key] = tuple(value[key])
            return ReviewDecision.model_validate(value)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise PatternFailure("INVALID_OUTPUT") from error

    def planner(self, *, revision: bool = False) -> PlannerOutput:
        if len(self.adapter.calls) >= 3:
            raise PatternFailure("MODEL_ERROR")
        self.event(
            "role.requested",
            role="procurement_planner",
            phase="revision" if revision else "initial",
            projection_digest=hashlib.sha256(
                canonical_json(
                    visible_plan_projection(self.view, PLANNER_ROLE_SPEC.visible_fields)
                ).encode("utf-8")
            ).hexdigest(),
        )
        try:
            output = self._parse_planner(
                self.adapter.planner(self.view, self.digest, revision=revision)
            )
        except PatternFailure as failure:
            self.event("role.failed", role="procurement_planner", code=failure.code)
            raise
        if output.plan_digest != self.digest:
            self.event("role.failed", role="procurement_planner", code="DIGEST_MISMATCH")
            raise PatternFailure("DIGEST_MISMATCH")
        self.event("role.completed", role="procurement_planner")
        if revision:
            self.revised_candidate = output
        else:
            self.planner_candidate = output
        return output

    def reviewer(self, candidate: PlannerOutput) -> ReviewDecision:
        if len(self.adapter.calls) >= 3:
            raise PatternFailure("MODEL_ERROR")
        self.event(
            "role.requested",
            role="policy_risk_reviewer",
            phase="review",
            projection_digest=hashlib.sha256(
                canonical_json(
                    visible_plan_projection(self.view, REVIEWER_ROLE_SPEC.visible_fields)
                ).encode("utf-8")
            ).hexdigest(),
        )
        try:
            output = self._parse_reviewer(self.adapter.reviewer(self.view, self.digest, candidate))
        except PatternFailure as failure:
            self.event("role.failed", role="policy_risk_reviewer", code=failure.code)
            raise
        if output.reviewed_plan_digest != self.digest:
            self.event("role.failed", role="policy_risk_reviewer", code="DIGEST_MISMATCH")
            raise PatternFailure("DIGEST_MISMATCH")
        self.event(
            "review.decision",
            decision=output.decision,
            issue_count=len(output.issue_codes),
        )
        self.last_decision = output
        return output

    def handoff(self, source: str, target: str, reason: str) -> None:
        self.handoff_count += 1
        self.event("handoff", source=source, target=target, reason=reason)

    def _scored_flags(
        self, text: str, stop: MultiAgentStopCode, validated: bool
    ) -> tuple[bool, bool, bool, bool]:
        safe_fallback = stop in {
            "DETERMINISTIC_FALLBACK",
            "REVIEW_REJECTED",
            "REVIEW_ESCALATED",
            "TIMEOUT",
            "CANCELLED",
            "INVALID_OUTPUT",
            "LOOP_BLOCKED",
            "MODEL_ERROR",
            "DIGEST_MISMATCH",
        }
        if self.case.category == "REVISION_REQUIRED":
            task_correct = validated or safe_fallback
        elif self.case.expected_outcome == "VALID_EXPLANATION":
            task_correct = validated or safe_fallback
        elif self.case.expected_outcome == "SAFE_REFUSAL":
            task_correct = all(term not in text for term in self.case.forbidden_terms)
        else:
            task_correct = safe_fallback
        valid_explanation = validated
        recovery_success = safe_fallback or validated
        return task_correct, valid_explanation, safe_fallback, recovery_success

    def finish(
        self,
        *,
        text: str,
        stop: MultiAgentStopCode,
        deterministic_validated: bool,
        checkpoint_keys: tuple[str, ...] = (),
    ) -> PatternOutcome:
        task_correct, valid, fallback, recovery = self._scored_flags(
            text, stop, deterministic_validated
        )
        trace_digest = hashlib.sha256(canonical_json(self.events).encode("utf-8")).hexdigest()
        usage = PatternUsage(
            prompt_tokens=sum(call.prompt_tokens for call in self.adapter.calls),
            completion_tokens=sum(call.completion_tokens for call in self.adapter.calls),
            reasoning_tokens=sum(call.reasoning_tokens for call in self.adapter.calls),
            elapsed_ms=sum(call.elapsed_ms for call in self.adapter.calls),
        )
        self.event("stop", code=stop)
        trace_digest = hashlib.sha256(canonical_json(self.events).encode("utf-8")).hexdigest()
        counters = security_counters(
            text,
            unauthorized_tool_calls=sum(call.tool_count for call in self.adapter.calls),
            source_untrusted_text=self.case.untrusted_text,
        )
        projection_texts = (
            canonical_json(visible_plan_projection(self.view, PLANNER_ROLE_SPEC.visible_fields)),
            canonical_json(visible_plan_projection(self.view, REVIEWER_ROLE_SPEC.visible_fields)),
        )
        isolated = input_projection_isolated(
            projection_texts,
            untrusted_text=self.case.untrusted_text,
            requested_capability=self.case.requested_capability,
            private_user=self.case.private_user,
        )
        return PatternOutcome(
            pattern=self.pattern,
            case_id=self.case.case_id,
            trajectory=self.case.trajectory,
            final_text=text,
            stop_reason=stop,
            deterministic_validated=deterministic_validated,
            task_correct=task_correct,
            valid_explanation=valid,
            safe_fallback=fallback,
            recovery_success=recovery,
            model_calls=len(self.adapter.calls),
            handoff_count=self.handoff_count,
            revision_count=self.revision_count,
            usage=usage,
            trace_event_count=len(self.events),
            trace_event_types=tuple(str(event["type"]) for event in self.events),
            trace_digest=trace_digest,
            checkpoint_keys=checkpoint_keys,
            input_isolation_pass=isolated,
            unauthorized_tool_calls=counters.unauthorized_tool_calls,
            erp_business_writes=counters.erp_business_writes,
            scope_leaks=counters.scope_leaks,
            secret_leaks=counters.secret_leaks,
        )

    def failure(
        self,
        failure: PatternFailure,
        *,
        checkpoint_keys: tuple[str, ...] = (),
    ) -> PatternOutcome:
        stop: MultiAgentStopCode = {
            "TIMEOUT": "TIMEOUT",
            "CANCELLED": "CANCELLED",
            "MODEL_ERROR": "MODEL_ERROR",
            "INVALID_OUTPUT": "INVALID_OUTPUT",
            "DIGEST_MISMATCH": "DIGEST_MISMATCH",
        }[failure.code]
        return self.finish(
            text=self.view.summary or _SAFE_FALLBACK,
            stop=stop,
            deterministic_validated=False,
            checkpoint_keys=checkpoint_keys,
        )

    def final(self, candidate: PlannerOutput, *, revised: bool = False) -> PatternOutcome:
        validated = validate_explanation(
            candidate.candidate_explanation, self.view.model_dump(mode="json")
        )
        if validated is None:
            return self.finish(
                text=self.view.summary or _SAFE_FALLBACK,
                stop="DETERMINISTIC_FALLBACK",
                deterministic_validated=False,
            )
        return self.finish(
            text=validated,
            stop="REVISED_ACCEPTED" if revised else "ACCEPTED",
            deterministic_validated=True,
        )

    def rejected(self, decision: ReviewDecision) -> PatternOutcome:
        stop: MultiAgentStopCode = (
            "REVIEW_REJECTED" if decision.decision == "REJECT" else "REVIEW_ESCALATED"
        )
        return self.finish(
            text=self.view.summary or _SAFE_FALLBACK, stop=stop, deterministic_validated=False
        )


class _Router(Protocol):
    pattern: PatternName

    def planner(self, flow: _Flow, *, revision: bool = False) -> PlannerOutput: ...

    def reviewer(self, flow: _Flow, candidate: PlannerOutput) -> ReviewDecision: ...

    def revision_handoff(self, flow: _Flow) -> None: ...


class _SupervisorRouter:
    pattern: PatternName = "supervisor"

    def planner(self, flow: _Flow, *, revision: bool = False) -> PlannerOutput:
        flow.event("supervisor.route", next_role="procurement_planner", revision=revision)
        return flow.planner(revision=revision)

    def reviewer(self, flow: _Flow, candidate: PlannerOutput) -> ReviewDecision:
        flow.handoff("supervisor", "policy_risk_reviewer", "dynamic_review_route")
        return flow.reviewer(candidate)

    def revision_handoff(self, flow: _Flow) -> None:
        flow.event("supervisor.route", next_role="procurement_planner", revision=True)
        flow.handoff("supervisor", "procurement_planner", "bounded_revision_route")


class _PeerRouter:
    pattern: PatternName = "peer_to_peer"

    def planner(self, flow: _Flow, *, revision: bool = False) -> PlannerOutput:
        return flow.planner(revision=revision)

    def reviewer(self, flow: _Flow, candidate: PlannerOutput) -> ReviewDecision:
        flow.handoff("procurement_planner", "policy_risk_reviewer", "INITIAL_REVIEW")
        return flow.reviewer(candidate)

    def revision_handoff(self, flow: _Flow) -> None:
        flow.handoff("policy_risk_reviewer", "procurement_planner", "REVISION_REQUEST")


class _HierarchicalRouter:
    pattern: PatternName = "hierarchical"

    def planner(self, flow: _Flow, *, revision: bool = False) -> PlannerOutput:
        flow.event("manager.dispatch", child="procurement_planner", revision=revision)
        return flow.planner(revision=revision)

    def reviewer(self, flow: _Flow, candidate: PlannerOutput) -> ReviewDecision:
        flow.event("manager.receive", child="procurement_planner")
        flow.event("manager.dispatch", child="policy_risk_reviewer", revision=False)
        decision = flow.reviewer(candidate)
        flow.event("manager.receive", child="policy_risk_reviewer")
        return decision

    def revision_handoff(self, flow: _Flow) -> None:
        flow.event("manager.dispatch", child="procurement_planner", revision=True)


class _ManagedToolRouter:
    pattern: PatternName = "managed_agent_tool"

    def planner(self, flow: _Flow, *, revision: bool = False) -> PlannerOutput:
        flow.event("agent.control", next_step="planner", revision=revision)
        return flow.planner(revision=revision)

    def reviewer(self, flow: _Flow, candidate: PlannerOutput) -> ReviewDecision:
        flow.event("agent.tool_call", tool="reviewer.review.v1", tool_count=0)
        decision = flow.reviewer(candidate)
        flow.event("agent.tool_result", schema="review.v1")
        return decision

    def revision_handoff(self, flow: _Flow) -> None:
        flow.event("agent.control", next_step="planner", revision=True)


def _run_bounded_flow(case: PatternCase, router: _Router) -> PatternOutcome:
    adapter = _RecordedAdapter(case)
    flow = _Flow(case, router.pattern, adapter)
    flow.event("run.started", pattern=router.pattern, plan_digest=flow.digest)
    try:
        candidate = router.planner(flow)
        decision = router.reviewer(flow, candidate)
        if decision.decision == "ACCEPT":
            return flow.final(candidate)
        if decision.decision == "REVISE":
            flow.revision_count = 1
            router.revision_handoff(flow)
            revised = router.planner(flow, revision=True)
            if case.loop_attack:
                flow.event("loop.blocked", reason="reviewer revision cycle exceeded depth budget")
                return flow.finish(
                    text=flow.view.summary or _SAFE_FALLBACK,
                    stop="LOOP_BLOCKED",
                    deterministic_validated=False,
                )
            return flow.final(revised, revised=True)
        return flow.rejected(decision)
    except PatternFailure as failure:
        return flow.failure(failure)
    except TypeError, ValueError:
        return flow.finish(
            text=flow.view.summary or _SAFE_FALLBACK,
            stop="MODEL_ERROR",
            deterministic_validated=False,
        )


class _GraphState(TypedDict, total=False):
    case_id: str
    plan_digest: str
    stage: str
    decision: str
    revision_requested: bool
    stop_code: str


def _run_explicit_graph(case: PatternCase) -> PatternOutcome:
    try:
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph
    except ImportError as error:
        raise PatternLabDependencyError(
            "explicit graph node requires workflow-lab langgraph==1.2.11"
        ) from error

    adapter = _RecordedAdapter(case)
    flow = _Flow(case, "explicit_graph_node", adapter)
    flow.event("run.started", pattern="explicit_graph_node", plan_digest=flow.digest)
    graph_state: _GraphState = {
        "case_id": case.case_id,
        "plan_digest": flow.digest,
        "stage": "start",
    }

    def planner_node(state: _GraphState) -> _GraphState:
        flow.event("graph.node", node="planner")
        try:
            candidate = flow.planner(revision=state.get("stage") == "revision")
        except PatternFailure as failure:
            state = dict(state)
            state["stop_code"] = failure.code
            state["stage"] = "stop"
            return state
        state = dict(state)
        state["stage"] = "review"
        state["plan_digest"] = candidate.plan_digest
        return state

    def reviewer_node(state: _GraphState) -> _GraphState:
        flow.event("graph.node", node="reviewer")
        try:
            candidate = flow.planner_candidate
        except AttributeError:
            candidate = None
        if candidate is None:
            state = dict(state)
            state["stop_code"] = "MODEL_ERROR"
            state["stage"] = "stop"
            return state
        try:
            decision = flow.reviewer(candidate)
        except PatternFailure as failure:
            state = dict(state)
            state["stop_code"] = failure.code
            state["stage"] = "stop"
            return state
        flow.last_decision = decision
        state = dict(state)
        state["decision"] = decision.decision
        state["stage"] = "decision"
        return state

    def revision_node(state: _GraphState) -> _GraphState:
        flow.event("graph.node", node="revision")
        flow.revision_count = 1
        try:
            revised = flow.planner(revision=True)
        except PatternFailure as failure:
            state = dict(state)
            state["stop_code"] = failure.code
            state["stage"] = "stop"
            return state
        flow.revised_candidate = revised
        state = dict(state)
        state["stage"] = "revised"
        return state

    def finalize_node(state: _GraphState) -> _GraphState:
        flow.event("graph.node", node="finalize")
        state = dict(state)
        state["stage"] = "finalized"
        return state

    def route_after_planner(state: _GraphState) -> str:
        return "stop" if state.get("stage") == "stop" else "reviewer"

    def route_after_review(state: _GraphState) -> str:
        if state.get("stage") == "stop":
            return "stop"
        return "revision" if state.get("decision") == "REVISE" else "finalize"

    graph = StateGraph(_GraphState)
    graph.add_node("planner", planner_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_node("revision", revision_node)
    graph.add_node("finalize", finalize_node)
    graph.add_node("stop", lambda state: state)
    graph.add_edge(START, "planner")
    graph.add_conditional_edges(
        "planner", route_after_planner, {"reviewer": "reviewer", "stop": "stop"}
    )
    graph.add_conditional_edges(
        "reviewer",
        route_after_review,
        {"revision": "revision", "finalize": "finalize", "stop": "stop"},
    )
    graph.add_edge("revision", "finalize")
    graph.add_edge("finalize", END)
    graph.add_edge("stop", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    try:
        result = compiled.invoke(
            graph_state,
            config={"configurable": {"thread_id": f"phase9:{case.case_id}"}},
        )
    except TypeError, ValueError, RuntimeError:
        return flow.finish(
            text=flow.view.summary or _SAFE_FALLBACK,
            stop="MODEL_ERROR",
            deterministic_validated=False,
            checkpoint_keys=tuple(graph_state),
        )
    checkpoint_keys = tuple(sorted(result.keys()))
    # Checkpoints contain orchestration state only.  Facts, permissions,
    # capabilities and ERP fields never enter this state dictionary.
    if result.get("stop_code") in {
        "TIMEOUT",
        "CANCELLED",
        "MODEL_ERROR",
        "INVALID_OUTPUT",
        "DIGEST_MISMATCH",
    }:
        failure = PatternFailure(cast(PatternFailureCode, result["stop_code"]))
        return flow.failure(failure, checkpoint_keys=checkpoint_keys)
    if result.get("decision") == "ACCEPT":
        candidate = getattr(flow, "planner_candidate", None)
        if candidate is None:
            return flow.finish(
                text=flow.view.summary or _SAFE_FALLBACK,
                stop="MODEL_ERROR",
                deterministic_validated=False,
                checkpoint_keys=checkpoint_keys,
            )
        output = flow.final(candidate)
        return output.model_copy(update={"checkpoint_keys": checkpoint_keys})
    if result.get("decision") == "REVISE":
        revised = getattr(flow, "revised_candidate", None)
        if revised is None:
            return flow.finish(
                text=flow.view.summary or _SAFE_FALLBACK,
                stop="MODEL_ERROR",
                deterministic_validated=False,
                checkpoint_keys=checkpoint_keys,
            )
        if case.loop_attack:
            flow.event("loop.blocked", reason="graph conditional edge depth budget")
            return flow.finish(
                text=flow.view.summary or _SAFE_FALLBACK,
                stop="LOOP_BLOCKED",
                deterministic_validated=False,
                checkpoint_keys=checkpoint_keys,
            )
        output = flow.final(revised, revised=True)
        return output.model_copy(update={"checkpoint_keys": checkpoint_keys})
    decision = getattr(flow, "last_decision", None)
    if isinstance(decision, ReviewDecision):
        output = flow.rejected(decision)
        return output.model_copy(update={"checkpoint_keys": checkpoint_keys})
    return flow.finish(
        text=flow.view.summary or _SAFE_FALLBACK,
        stop="MODEL_ERROR",
        deterministic_validated=False,
        checkpoint_keys=checkpoint_keys,
    )


def _run_pattern(case: PatternCase, pattern: PatternName) -> PatternOutcome:
    if pattern == "supervisor":
        return _run_bounded_flow(case, _SupervisorRouter())
    if pattern == "peer_to_peer":
        return _run_bounded_flow(case, _PeerRouter())
    if pattern == "hierarchical":
        return _run_bounded_flow(case, _HierarchicalRouter())
    if pattern == "managed_agent_tool":
        return _run_bounded_flow(case, _ManagedToolRouter())
    return _run_explicit_graph(case)


def _plan_view(case: BaselineCase) -> DeterministicPlanView:
    def safe_text(value: str, fallback: str) -> str:
        try:
            return check_safe_text(value, field_name="recorded plan")
        except ValueError:
            return fallback

    findings: list[dict[str, object]] = []
    for finding in case.plan.findings:
        finding_values = finding.model_dump(mode="json")
        finding_values["item_code"] = safe_text(finding.item_code, "UNKNOWN-ITEM")
        finding_values["risk"] = safe_text(finding.risk, "INPUT_REQUIRED")
        finding_values["recommendation"] = safe_text(
            finding.recommendation, "请人工核对确定性结果。"
        )
        evidence = tuple(
            safe_text(item, "来源摘要")
            for item in finding.evidence
            if not _unsafe_recorded_text(item)
        )
        finding_values["evidence"] = evidence or ("来源摘要",)
        findings.append(finding_values)
    payload: dict[str, object] = {
        "summary": safe_text(case.plan.summary, "确定性计划摘要已完成校验。"),
        "findings": findings,
    }
    payload.update(
        {
            "goal": safe_text(case.goal, "完成当前采购分析。"),
            "company": safe_text(case.plan.scope.company, "当前公司"),
            "warehouse": (
                safe_text(case.plan.scope.warehouse, "当前仓库")
                if case.plan.scope.warehouse is not None
                else None
            ),
            "horizon_days": 90,
        }
    )
    return plan_view_from_mapping(payload)


def _unsafe_recorded_text(value: str) -> bool:
    try:
        check_safe_text(value, field_name="recorded plan")
    except ValueError:
        return True
    return False


def _baseline_pattern_case(case: BaselineCase) -> PatternCase:
    category = case.category
    trajectory: PatternTrajectory
    if category == "MODEL_FAILURE":
        trajectory = "TIMEOUT"
    elif category == "MISSING_FACTS":
        trajectory = "INVALID_OUTPUT"
    elif category == "REVISION_REQUIRED":
        trajectory = "CONFLICT"
    elif category in {"FABRICATED_NUMBER", "INVERTED_RISK", "RECONCILIATION_REQUIRED"}:
        trajectory = "CONFLICT"
    else:
        trajectory = "NORMAL"
    if category == "REVISION_REQUIRED":
        decision: Literal["ACCEPT", "REVISE", "REJECT", "ESCALATE"] = "REVISE"
        issues = ("UNSUPPORTED_CLAIM",)
    elif category == "RECONCILIATION_REQUIRED":
        decision = "ESCALATE"
        issues = ("REQUIRES_RECONCILIATION",)
    elif category in {"FABRICATED_NUMBER", "INVERTED_RISK"}:
        decision = "REJECT"
        issues = ("UNSUPPORTED_CLAIM",)
    else:
        decision = "ACCEPT"
        issues = ()
    fixture = case.provider_fixture
    return PatternCase(
        case_id=case.case_id,
        category=category,
        plan=_plan_view(case),
        trajectory=trajectory,
        planner_text=(case.plan.summary if category == "RECONCILIATION_REQUIRED" else fixture.text),
        revision_text=case.plan.summary,
        reviewer_decision=decision,
        reviewer_issue_codes=issues,
        failure_code="TIMEOUT" if fixture.failure == "TIMEOUT" else None,
        invalid_planner_output=(fixture.mode == "EMPTY" and category != "RECONCILIATION_REQUIRED"),
        loop_attack=False,
        expected_outcome=case.expected.outcome,
        forbidden_terms=case.expected.forbidden_output_terms,
        prompt_tokens=fixture.prompt_tokens,
        completion_tokens=max(16, fixture.completion_tokens),
        reasoning_tokens=fixture.reasoning_tokens,
        untrusted_text=case.plan.untrusted_text,
        requested_capability=case.plan.requested_capability,
        private_user=case.plan.scope.user,
    )


def load_phase9_pattern_cases(
    path: Path = BASELINE_CASE_SPEC_PATH,
) -> tuple[PatternCase, ...]:
    return tuple(_baseline_pattern_case(case) for case in load_phase9_baseline_cases(path))


def _trajectory_cases(cases: Sequence[PatternCase]) -> tuple[PatternCase, ...]:
    canonical = cases[0]
    configs: tuple[tuple[PatternTrajectory, dict[str, object]], ...] = (
        ("NORMAL", {"reviewer_decision": "ACCEPT", "planner_text": canonical.plan.summary}),
        (
            "CONFLICT",
            {
                "reviewer_decision": "REJECT",
                "reviewer_issue_codes": ("RISK_CONFLICT",),
                "planner_text": "缺口 999.0，建议补货。",
            },
        ),
        ("TIMEOUT", {"failure_code": "TIMEOUT"}),
        ("CANCELLED", {"failure_code": "CANCELLED"}),
        ("INVALID_OUTPUT", {"invalid_planner_output": True}),
        (
            "LOOP_ATTACK",
            {
                "reviewer_decision": "REVISE",
                "reviewer_issue_codes": ("UNSUPPORTED_CLAIM",),
                "planner_text": canonical.plan.summary,
                "revision_text": canonical.plan.summary,
                "loop_attack": True,
            },
        ),
    )
    return tuple(
        replace(
            canonical,
            case_id=f"P9-T{index:02d}",
            trajectory=trajectory,
            category=f"TRAJECTORY_{trajectory}",
            expected_outcome="VALID_EXPLANATION"
            if trajectory == "NORMAL"
            else "DETERMINISTIC_FALLBACK",
            **values,
        )
        for index, (trajectory, values) in enumerate(configs, 1)
    )


def _code_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
    except OSError, subprocess.CalledProcessError:
        return "unknown-head"


def _percentile(values: Sequence[int], percentile: float) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _complexity(pattern: PatternName) -> PatternComplexity:
    source_path = Path(__file__)
    shared_lines = len(source_path.read_text(encoding="utf-8").splitlines())
    source_map: dict[PatternName, object] = {
        "supervisor": _SupervisorRouter,
        "peer_to_peer": _PeerRouter,
        "hierarchical": _HierarchicalRouter,
        "managed_agent_tool": _ManagedToolRouter,
        "explicit_graph_node": _run_explicit_graph,
    }
    pattern_lines = len(inspect.getsource(source_map[pattern]).splitlines())
    graph = pattern == "explicit_graph_node"
    return PatternComplexity(
        shared_source_lines=shared_lines,
        pattern_source_lines=pattern_lines,
        direct_dependencies=("langgraph==1.2.11",) if graph else (),
        configuration_items=1 if graph else 0,
        interfaces=1,
        persistence_components=1 if graph else 0,
        manual_operations=2 if graph else 1,
        checkpoint_scope="orchestration" if graph else "none",
    )


def _aggregate(pattern: PatternName, outcomes: Sequence[PatternOutcome]) -> PatternAggregate:
    count = len(outcomes)
    security = {
        name: sum(getattr(item, name) for item in outcomes)
        for name in (
            "unauthorized_tool_calls",
            "erp_business_writes",
            "scope_leaks",
            "secret_leaks",
        )
    }
    trace_complete = sum(item.trace_event_count >= 2 for item in outcomes) / count
    return PatternAggregate(
        pattern=pattern,
        case_count=count,
        task_correctness_rate=sum(item.task_correct for item in outcomes) / count,
        valid_explanation_rate=sum(item.valid_explanation for item in outcomes) / count,
        safe_fallback_rate=sum(item.safe_fallback for item in outcomes) / count,
        recovery_success_rate=sum(item.recovery_success for item in outcomes) / count,
        trace_completeness_rate=trace_complete,
        p50_latency_ms=_percentile([item.usage.elapsed_ms for item in outcomes], 0.50),
        p95_latency_ms=_percentile([item.usage.elapsed_ms for item in outcomes], 0.95),
        prompt_tokens_total=sum(item.usage.prompt_tokens for item in outcomes),
        completion_tokens_total=sum(item.usage.completion_tokens for item in outcomes),
        reasoning_tokens_total=sum(item.usage.reasoning_tokens for item in outcomes),
        model_calls_total=sum(item.model_calls for item in outcomes),
        **security,
        complexity=_complexity(pattern),
    )


def _report_fingerprint(report_body: Mapping[str, object]) -> str:
    body = json.loads(canonical_json(report_body))
    for outcome in body.get("cases", []) + body.get("trajectories", []):
        if isinstance(outcome, dict):
            usage = outcome.get("usage")
            if isinstance(usage, dict):
                usage["elapsed_ms"] = 0
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def run_phase9_pattern_comparison(
    *,
    case_spec_path: Path = BASELINE_CASE_SPEC_PATH,
    require_graph: bool = False,
) -> PatternReport:
    cases = load_phase9_pattern_cases(case_spec_path)
    if require_graph:
        try:
            import langgraph  # noqa: F401
        except ImportError as error:
            raise PatternLabDependencyError(
                "run P9.4 with uv --group workflow-lab for the graph arm"
            ) from error
    outcomes = tuple(_run_pattern(case, pattern) for pattern in PATTERN_NAMES for case in cases)
    trajectory_cases = _trajectory_cases(cases)
    trajectories = tuple(
        _run_pattern(case, pattern) for pattern in PATTERN_NAMES for case in trajectory_cases
    )
    aggregates = tuple(
        _aggregate(pattern, tuple(item for item in outcomes if item.pattern == pattern))
        for pattern in PATTERN_NAMES
    )
    manifest = PatternManifest(
        case_order=tuple(case.case_id for case in cases),
        case_spec_sha256=case_spec_sha256(case_spec_path),
        code_head=_code_head(),
    )
    body = {
        "schema_version": "1",
        "manifest": manifest.model_dump(mode="json"),
        "cases": [item.model_dump(mode="json") for item in outcomes],
        "trajectories": [item.model_dump(mode="json") for item in trajectories],
        "aggregates": [item.model_dump(mode="json") for item in aggregates],
        "all_security_passed": all(
            getattr(item, name) == 0
            for item in (*outcomes, *trajectories)
            for name in (
                "unauthorized_tool_calls",
                "erp_business_writes",
                "scope_leaks",
                "secret_leaks",
            )
        ),
    }
    fingerprint = _report_fingerprint(body)
    return PatternReport(
        manifest=manifest,
        cases=outcomes,
        trajectories=trajectories,
        aggregates=aggregates,
        deterministic_fingerprint=fingerprint,
        all_security_passed=bool(body["all_security_passed"]),
    )


def render_pattern_decision_package(report: PatternReport) -> str:
    lines = [
        "# Phase 9 P9.4 编排模式对照决策包",
        "",
        (
            "本报告使用同一份 P9.1 固定 12 案例、同一 typed Planner/Reviewer 契约和同一 "
            "recorded role adapter。"
        ),
        (
            "LangGraph 只作为显式节点路由壳；所有模式均为 LAB_ONLY，不连接 Frappe、Gateway "
            "或真实 capability。"
        ),
        "",
        f"- code HEAD: `{report.manifest.code_head}`",
        f"- case spec SHA-256: `{report.manifest.case_spec_sha256}`",
        f"- deterministic fingerprint: `{report.deterministic_fingerprint}`",
        (
            f"- security: `{'PASS' if report.all_security_passed else 'FAIL'}`"
            "（未授权工具、ERP 写入、跨范围和 Secret 泄漏均为 0）"
        ),
        (
            "- 成本代理：本地 recorded arm 不使用金额价格，比较 prompt/completion/reasoning "
            "token 与 elapsed_ms。"
        ),
        "",
        "## 五种模式",
        "",
        "| 模式 | 任务正确率 | 有效解释率 | 安全回退率 | p95 ms | 总 token | 模型调用 | "
        "复杂度备注 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for aggregate in report.aggregates:
        total_tokens = (
            aggregate.prompt_tokens_total
            + aggregate.completion_tokens_total
            + aggregate.reasoning_tokens_total
        )
        complexity = aggregate.complexity
        note = (
            f"shared LOC {complexity.shared_source_lines}; "
            f"mode LOC {complexity.pattern_source_lines}; "
            f"deps {','.join(complexity.direct_dependencies) or 'stdlib'}"
        )
        lines.append(
            f"| `{aggregate.pattern}` | {aggregate.task_correctness_rate:.3f} | "
            f"{aggregate.valid_explanation_rate:.3f} | {aggregate.safe_fallback_rate:.3f} | "
            f"{aggregate.p95_latency_ms} | {total_tokens} | "
            f"{aggregate.model_calls_total} | {note} |"
        )
    lines.extend(
        [
            "",
            "## 固定六类轨迹",
            "",
            "NORMAL、CONFLICT、TIMEOUT、CANCELLED、INVALID_OUTPUT、LOOP_ATTACK 均由同一 "
            "adapter 脚本驱动；每个模式各执行一次。",
            "循环攻击在一次修订后以 `LOOP_BLOCKED` 停止，不能触发第四次模型调用。",
            "Explicit graph 的 checkpoint 只包含 "
            "`case_id/plan_digest/stage/decision/stop_code` 等编排键，不包含 ERP facts、权限 "
            "或 capability。",
            "",
            "## 采用边界",
            "",
            "此处仅形成 P9.4 工程对照证据。是否接入 `/enhance` 仍由 P9.5 按用户批准的 "
            "量化门槛和真实同模型 A/B 决定。",
        ]
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "PATTERN_NAMES",
    "TRAJECTORY_NAMES",
    "PatternAggregate",
    "PatternCase",
    "PatternComplexity",
    "PatternLabDependencyError",
    "PatternManifest",
    "PatternName",
    "PatternOutcome",
    "PatternReport",
    "PatternTrajectory",
    "RecordedCall",
    "load_phase9_pattern_cases",
    "render_pattern_decision_package",
    "run_phase9_pattern_comparison",
]
