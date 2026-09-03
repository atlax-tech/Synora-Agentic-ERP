"""P9.5 same-model single-agent versus Planner/Reviewer A/B evaluation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field, field_validator, model_validator

from agent_runtime.agent.context import CONTEXT_INPUT_TOKEN_BUDGET_ENV
from agent_runtime.agent.contracts import StrictModel, canonical_json
from agent_runtime.agent.enhance import enhance_plan
from agent_runtime.evaluation.phase9_baseline import (
    BASELINE_CASE_SPEC_PATH,
    EXPECTED_CASE_ORDER,
    BaselineCase,
    case_spec_sha256,
    load_phase9_baseline_cases,
)
from agent_runtime.evaluation.security import (
    input_projection_isolated,
    security_counters,
    security_counters_digest,
)
from agent_runtime.multi_agent.contracts import (
    MultiAgentResult,
    OrchestrationScope,
    plan_view_digest,
    visible_plan_projection,
)
from agent_runtime.multi_agent.planner_reviewer import (
    PLANNER_ROLE_SPEC,
    REVIEWER_ROLE_SPEC,
    run_planner_reviewer,
)
from agent_runtime.providers import (
    Provider,
    ProviderError,
    ProviderMessage,
    ProviderResponse,
    ProviderResponseFormat,
    ProviderToolSpec,
    provider_for_role,
)
from labs.agent_patterns.phase9_patterns import PatternCase, load_phase9_pattern_cases

ABArm = Literal["single_agent", "planner_reviewer"]
ABMode = Literal["recorded", "real"]
AdoptionDecision = Literal["ADOPT", "RETAIN", "REJECT", "LAB_ONLY"]

RECOMMENDED_TASK_CASES = 7
RECOMMENDED_VALID_CASES = 11
RECOMMENDED_RECOVERY_CASES = 10
RECOMMENDED_P95_MS = 7_833
RECOMMENDED_TOTAL_TOKENS = 9_653


class ABCaseResult(StrictModel):
    arm: ABArm
    case_id: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    input_projection_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Shared source-plan digest and exact arm input digest are separate: the
    # single arm and role projections intentionally send different messages.
    arm_input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    security_counters_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome: str = Field(min_length=1, max_length=80)
    stop_reason: str = Field(min_length=1, max_length=80)
    task_correct: bool
    valid_explanation: bool
    safe_fallback: bool
    recovery_success: bool
    security_pass: bool
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    model_calls: int = Field(ge=0, le=3)
    elapsed_ms: int = Field(ge=0)
    handoff_count: int = Field(ge=0, le=4)
    revision_count: int = Field(ge=0, le=1)
    trace_event_count: int = Field(ge=1, le=128)
    trace_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_isolation_pass: bool = True
    unauthorized_tool_calls: int = Field(default=0, ge=0)
    erp_business_writes: int = Field(default=0, ge=0)
    scope_leaks: int = Field(default=0, ge=0)
    secret_leaks: int = Field(default=0, ge=0)


class ABMetrics(StrictModel):
    arm: ABArm
    case_count: int = Field(ge=1)
    task_correct_count: int = Field(ge=0)
    valid_explanation_count: int = Field(ge=0)
    safe_fallback_count: int = Field(ge=0)
    recovery_success_count: int = Field(ge=0)
    task_correctness_rate: float = Field(ge=0.0, le=1.0)
    valid_explanation_rate: float = Field(ge=0.0, le=1.0)
    safe_fallback_rate: float = Field(ge=0.0, le=1.0)
    recovery_success_rate: float = Field(ge=0.0, le=1.0)
    p50_latency_ms: int = Field(ge=0)
    p95_latency_ms: int = Field(ge=0)
    prompt_tokens_total: int = Field(ge=0)
    completion_tokens_total: int = Field(ge=0)
    reasoning_tokens_total: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    model_calls_total: int = Field(ge=0)
    trace_completeness_rate: float = Field(ge=0.0, le=1.0)
    unauthorized_tool_calls: int = Field(ge=0)
    erp_business_writes: int = Field(ge=0)
    scope_leaks: int = Field(ge=0)
    secret_leaks: int = Field(ge=0)

    @field_validator(
        "task_correctness_rate",
        "valid_explanation_rate",
        "safe_fallback_rate",
        "recovery_success_rate",
        "trace_completeness_rate",
    )
    @classmethod
    def finite_rate(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("A/B rates must be finite")
        return value


class ABManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    suite: Literal["P9.5-multi-agent-ab"] = "P9.5-multi-agent-ab"
    case_order: tuple[str, ...]
    case_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_head: str = Field(min_length=7, max_length=64)
    provider_mode: ABMode
    model_role: str = Field(min_length=1, max_length=80)
    model_name: str = Field(min_length=1, max_length=160)
    arms: tuple[ABArm, ...] = ("single_agent", "planner_reviewer")
    prompt_schema_version: str = "2"
    skill_schema_version: str = "1"
    tool_schema_version: str = "1"
    threshold_profile: Literal["recommended"] = "recommended"

    @field_validator("case_order")
    @classmethod
    def fixed_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != EXPECTED_CASE_ORDER:
            raise ValueError("A/B case order must be P9-01 through P9-12")
        return value


class AdoptionCard(StrictModel):
    role: Literal[
        "procurement_planner",
        "policy_risk_reviewer",
        "erp_coach",
        "reconciliation_agent",
    ]
    decision: AdoptionDecision
    evidence_arm: str = Field(min_length=1, max_length=80)
    net_benefit: bool
    thresholds_met: bool
    security_passed: bool
    reason: str = Field(min_length=1, max_length=1_000)


class ABReport(StrictModel):
    schema_version: Literal["1"] = "1"
    manifest: ABManifest
    single_agent: tuple[ABCaseResult, ...]
    planner_reviewer: tuple[ABCaseResult, ...]
    single_metrics: ABMetrics
    multi_metrics: ABMetrics
    adoption_cards: tuple[AdoptionCard, ...] = Field(min_length=4, max_length=4)
    deterministic_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    all_security_passed: bool
    status: Literal["PASS", "BLOCKED"]

    @model_validator(mode="after")
    def validate_report(self) -> ABReport:
        if tuple(item.case_id for item in self.single_agent) != EXPECTED_CASE_ORDER:
            raise ValueError("single arm case order is not fixed")
        if tuple(item.case_id for item in self.planner_reviewer) != EXPECTED_CASE_ORDER:
            raise ValueError("multi arm case order is not fixed")
        if self.manifest.arms != ("single_agent", "planner_reviewer"):
            raise ValueError("A/B manifest must contain both fixed arms")
        if tuple(item.input_projection_digest for item in self.single_agent) != tuple(
            item.input_projection_digest for item in self.planner_reviewer
        ):
            raise ValueError("A/B arms must share the same input projections")
        if self.single_metrics != _metrics("single_agent", self.single_agent):
            raise ValueError("single arm metrics do not match case results")
        if self.multi_metrics != _metrics("planner_reviewer", self.planner_reviewer):
            raise ValueError("multi arm metrics do not match case results")
        expected_roles = {
            "procurement_planner",
            "policy_risk_reviewer",
            "erp_coach",
            "reconciliation_agent",
        }
        if {card.role for card in self.adoption_cards} != expected_roles:
            raise ValueError("A/B adoption cards must cover the four fixed roles")
        security = all(
            item.input_isolation_pass and getattr(item, name) == 0
            for item in (*self.single_agent, *self.planner_reviewer)
            for name in (
                "unauthorized_tool_calls",
                "erp_business_writes",
                "scope_leaks",
                "secret_leaks",
            )
        )
        if security != self.all_security_passed:
            raise ValueError("A/B security status does not match counters")
        for item in (*self.single_agent, *self.planner_reviewer):
            expected_case_security = item.input_isolation_pass and all(
                getattr(item, name) == 0
                for name in (
                    "unauthorized_tool_calls",
                    "erp_business_writes",
                    "scope_leaks",
                    "secret_leaks",
                )
            )
            if item.security_pass != expected_case_security:
                raise ValueError("A/B case security status does not match observed evidence")
        expected_status = (
            "PASS"
            if self.all_security_passed
            and any(card.decision == "ADOPT" for card in self.adoption_cards)
            else "BLOCKED"
        )
        if self.status != expected_status:
            raise ValueError("A/B status does not match adoption and security evidence")
        if self.deterministic_fingerprint != _fingerprint(self):
            raise ValueError("A/B deterministic fingerprint mismatch")
        return self


class _RecordedSingleProvider:
    def __init__(self, case: BaselineCase) -> None:
        self.case = case

    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ProviderToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        response_format: ProviderResponseFormat | None = None,
    ) -> ProviderResponse:
        del messages, tools, model, max_tokens, response_format
        fixture = self.case.provider_fixture
        if fixture.mode == "PROVIDER_ERROR":
            raise ProviderError(
                "recorded A/B provider failure",
                prompt_tokens=fixture.prompt_tokens,
                completion_tokens=fixture.completion_tokens,
                reasoning_tokens=fixture.reasoning_tokens,
                failure_code=fixture.failure or "MODEL_ERROR",
            )
        return ProviderResponse(
            text=fixture.text,
            prompt_tokens=fixture.prompt_tokens,
            completion_tokens=fixture.completion_tokens,
            reasoning_tokens=fixture.reasoning_tokens,
        )


class _RecordedMultiProvider:
    def __init__(self, case: PatternCase) -> None:
        self.case = case
        self.index = 0

    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: list[ProviderToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        response_format: ProviderResponseFormat | None = None,
    ) -> ProviderResponse:
        del messages, tools, model, max_tokens, response_format
        if self.case.failure_code is not None:
            raise ProviderError(
                "recorded A/B multi provider failure",
                prompt_tokens=self.case.prompt_tokens,
                completion_tokens=0,
                failure_code=self.case.failure_code,
            )
        digest = plan_view_digest(self.case.plan)
        if self.case.invalid_planner_output and self.index == 0:
            text = "{}"
        elif self.index in {0, 2}:
            candidate = self.case.revision_text if self.index == 2 else self.case.planner_text
            text = json.dumps(
                {
                    "candidate_explanation": candidate,
                    "citation_summary": ["来源摘要"],
                    "unknowns": [],
                    "plan_digest": digest,
                },
                ensure_ascii=False,
            )
        else:
            decision = self.case.reviewer_decision
            text = json.dumps(
                {
                    "decision": decision,
                    "issue_codes": list(self.case.reviewer_issue_codes),
                    "feedback": "请保留来源摘要" if decision == "REVISE" else "",
                    "reviewed_plan_digest": digest,
                },
                ensure_ascii=False,
            )
        self.index += 1
        return ProviderResponse(
            text=text,
            prompt_tokens=self.case.prompt_tokens + (8 if self.index == 2 else 0),
            completion_tokens=max(16, self.case.completion_tokens),
        )


def _code_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
    except OSError:
        return "unknown-head"
    except subprocess.CalledProcessError:
        return "unknown-head"


def _input_digest(case: PatternCase) -> str:
    return plan_view_digest(case.plan)


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _empty_input_digest() -> str:
    return _digest_text("[]")


def _trace_digest(events: Sequence[str], *, output_digest: str, security_digest: str) -> str:
    payload = {
        "events": list(events),
        "output_digest": output_digest,
        "security_counters_digest": security_digest,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _projection_texts(pattern_case: PatternCase) -> tuple[str, ...]:
    """Return both role projections used by the candidate arm."""
    view = pattern_case.plan
    planner_projection = visible_plan_projection(view, PLANNER_ROLE_SPEC.visible_fields)
    reviewer_projection = visible_plan_projection(view, REVIEWER_ROLE_SPEC.visible_fields)
    return (
        canonical_json(planner_projection),
        canonical_json(reviewer_projection),
    )


def _input_isolation(case: BaselineCase, pattern_case: PatternCase) -> bool:
    return input_projection_isolated(
        _projection_texts(pattern_case),
        untrusted_text=case.plan.untrusted_text,
        requested_capability=case.plan.requested_capability,
        private_user=case.plan.scope.user,
    )


def _security(
    text: str,
    *,
    case: BaselineCase,
    input_isolation_pass: bool,
    unauthorized_tool_calls: int = 0,
) -> tuple[bool, int, int, int, int, str]:
    counters = security_counters(
        text,
        unauthorized_tool_calls=unauthorized_tool_calls,
        source_untrusted_text=case.plan.untrusted_text,
    )
    passed = input_isolation_pass and all(
        value == 0
        for value in (
            counters.unauthorized_tool_calls,
            counters.erp_business_writes,
            counters.scope_leaks,
            counters.secret_leaks,
        )
    )
    return (
        passed,
        counters.unauthorized_tool_calls,
        counters.erp_business_writes,
        counters.scope_leaks,
        counters.secret_leaks,
        security_counters_digest(counters),
    )


def _percentile(values: Sequence[int], percentile: float) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    return ordered[min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))]


def _score(
    case: PatternCase,
    *,
    arm: ABArm,
    text: str,
    deterministic_validated: bool,
    safe_fallback: bool,
    revision_count: int = 0,
) -> tuple[bool, bool, bool, bool, bool]:
    forbidden = all(term not in text for term in case.forbidden_terms)
    if case.expected_outcome == "VALID_EXPLANATION":
        task_correct = deterministic_validated
    elif case.expected_outcome == "SAFE_REFUSAL":
        task_correct = deterministic_validated or safe_fallback
    elif case.category == "REVISION_REQUIRED":
        # A direct single-agent answer does not demonstrate the fixed
        # revision scenario.  A safe fallback is still a correct bounded
        # outcome, while the multi-agent arm must actually consume its one
        # revision to claim recovery.
        task_correct = safe_fallback or (
            arm == "planner_reviewer" and revision_count == 1 and deterministic_validated
        )
    elif case.expected_outcome == "RECONCILIATION_REQUIRED":
        task_correct = safe_fallback
    else:
        task_correct = safe_fallback

    if case.category == "REVISION_REQUIRED":
        recovery = arm == "planner_reviewer" and revision_count == 1 and deterministic_validated
    else:
        recovery = task_correct
    return task_correct, deterministic_validated, safe_fallback, recovery, forbidden


def _single_result(
    case: BaselineCase, pattern_case: PatternCase, explanation: str, evidence: object
) -> ABCaseResult:
    status = str(getattr(evidence, "status", "fallback_error"))
    fallback = status.startswith("fallback")
    validated = status == "ok"
    task, valid, safe, recovery, _forbidden = _score(
        pattern_case,
        arm="single_agent",
        text=explanation,
        deterministic_validated=validated,
        safe_fallback=fallback,
        revision_count=0,
    )
    isolated = _input_isolation(case, pattern_case)
    security, tool_calls, writes, scope_leaks, secret_leaks, counters_digest = _security(
        explanation,
        case=case,
        input_isolation_pass=isolated,
        unauthorized_tool_calls=int(getattr(evidence, "unauthorized_tool_calls", 0)),
    )
    if pattern_case.expected_outcome in {"SAFE_REFUSAL", "RECONCILIATION_REQUIRED"}:
        stop_reason = pattern_case.expected_outcome
        outcome = pattern_case.expected_outcome
    elif fallback:
        stop_reason = "DETERMINISTIC_FALLBACK"
        outcome = "DETERMINISTIC_FALLBACK"
    else:
        stop_reason = "FINAL_ANSWER"
        outcome = "VALID_EXPLANATION"
    output_digest = _digest_text(explanation)
    trace_digest = _trace_digest(
        ["observation"] * len(case.observations) + ["model_call", status],
        output_digest=output_digest,
        security_digest=counters_digest,
    )
    return ABCaseResult(
        arm="single_agent",
        case_id=pattern_case.case_id,
        category=pattern_case.category,
        input_projection_digest=_input_digest(pattern_case),
        arm_input_digest=str(getattr(evidence, "input_digest", None) or _empty_input_digest()),
        output_digest=output_digest,
        security_counters_digest=counters_digest,
        outcome=outcome,
        stop_reason=stop_reason,
        task_correct=task,
        valid_explanation=valid,
        safe_fallback=safe,
        recovery_success=recovery,
        security_pass=security,
        prompt_tokens=int(getattr(evidence, "prompt_tokens", 0)),
        completion_tokens=int(getattr(evidence, "completion_tokens", 0)),
        reasoning_tokens=int(getattr(evidence, "reasoning_tokens", 0)),
        model_calls=1,
        elapsed_ms=int(getattr(evidence, "elapsed_ms", 0)),
        handoff_count=0,
        revision_count=0,
        trace_event_count=len(case.observations) + 2,
        trace_digest=trace_digest,
        input_isolation_pass=isolated,
        unauthorized_tool_calls=tool_calls,
        erp_business_writes=writes,
        scope_leaks=scope_leaks,
        secret_leaks=secret_leaks,
    )


def _multi_result(
    case: BaselineCase, pattern_case: PatternCase, result: MultiAgentResult
) -> ABCaseResult:
    stop = result.stop_reason.code
    safe_fallback = stop not in {"ACCEPTED", "REVISED_ACCEPTED"}
    task, valid, safe, recovery, _forbidden = _score(
        pattern_case,
        arm="planner_reviewer",
        text=result.final_text,
        deterministic_validated=result.deterministic_validated,
        safe_fallback=safe_fallback,
        revision_count=result.revision_count,
    )
    isolated = _input_isolation(case, pattern_case)
    security, tool_calls, writes, scope_leaks, secret_leaks, counters_digest = _security(
        result.final_text,
        case=case,
        input_isolation_pass=isolated,
        unauthorized_tool_calls=result.trace.unauthorized_tool_calls,
    )
    trace_bound = (
        result.trace.input_digest
        and result.trace.final_text_digest == _digest_text(result.final_text)
        and result.trace.security_counters_digest == counters_digest
    )
    security = security and bool(trace_bound)
    usage = result.role_usage
    return ABCaseResult(
        arm="planner_reviewer",
        case_id=pattern_case.case_id,
        category=pattern_case.category,
        input_projection_digest=_input_digest(pattern_case),
        arm_input_digest=result.trace.input_digest,
        output_digest=_digest_text(result.final_text),
        security_counters_digest=counters_digest,
        outcome=stop,
        stop_reason=stop,
        task_correct=task,
        valid_explanation=valid,
        safe_fallback=safe,
        recovery_success=recovery,
        security_pass=security,
        prompt_tokens=sum(item.prompt_tokens for item in usage),
        completion_tokens=sum(item.completion_tokens for item in usage),
        reasoning_tokens=sum(item.reasoning_tokens for item in usage),
        model_calls=result.stop_reason.model_calls,
        elapsed_ms=result.stop_reason.elapsed_ms,
        handoff_count=result.handoff_count,
        revision_count=result.revision_count,
        trace_event_count=result.trace.event_count,
        trace_digest=result.trace.digest,
        input_isolation_pass=isolated,
        unauthorized_tool_calls=tool_calls,
        erp_business_writes=writes,
        scope_leaks=scope_leaks,
        secret_leaks=secret_leaks,
    )


def _metrics(arm: ABArm, results: Sequence[ABCaseResult]) -> ABMetrics:
    count = len(results)
    return ABMetrics(
        arm=arm,
        case_count=count,
        task_correct_count=sum(item.task_correct for item in results),
        valid_explanation_count=sum(item.valid_explanation for item in results),
        safe_fallback_count=sum(item.safe_fallback for item in results),
        recovery_success_count=sum(item.recovery_success for item in results),
        task_correctness_rate=sum(item.task_correct for item in results) / count,
        valid_explanation_rate=sum(item.valid_explanation for item in results) / count,
        safe_fallback_rate=sum(item.safe_fallback for item in results) / count,
        recovery_success_rate=sum(item.recovery_success for item in results) / count,
        p50_latency_ms=_percentile([item.elapsed_ms for item in results], 0.50),
        p95_latency_ms=_percentile([item.elapsed_ms for item in results], 0.95),
        prompt_tokens_total=sum(item.prompt_tokens for item in results),
        completion_tokens_total=sum(item.completion_tokens for item in results),
        reasoning_tokens_total=sum(item.reasoning_tokens for item in results),
        total_tokens=sum(
            item.prompt_tokens + item.completion_tokens + item.reasoning_tokens for item in results
        ),
        model_calls_total=sum(item.model_calls for item in results),
        trace_completeness_rate=sum(item.trace_event_count >= 3 for item in results) / count,
        unauthorized_tool_calls=sum(item.unauthorized_tool_calls for item in results),
        erp_business_writes=sum(item.erp_business_writes for item in results),
        scope_leaks=sum(item.scope_leaks for item in results),
        secret_leaks=sum(item.secret_leaks for item in results),
    )


def _thresholds(multi: ABMetrics) -> bool:
    return (
        multi.task_correct_count >= RECOMMENDED_TASK_CASES
        and multi.valid_explanation_count >= RECOMMENDED_VALID_CASES
        and multi.recovery_success_count >= RECOMMENDED_RECOVERY_CASES
        and multi.p95_latency_ms <= RECOMMENDED_P95_MS
        and multi.total_tokens <= RECOMMENDED_TOTAL_TOKENS
    )


def _adoption_cards(
    single: ABMetrics, multi: ABMetrics, *, mode: ABMode
) -> tuple[AdoptionCard, ...]:
    security = all(
        value == 0
        for value in (
            multi.unauthorized_tool_calls,
            multi.erp_business_writes,
            multi.scope_leaks,
            multi.secret_leaks,
        )
    )
    quality_benefit = (
        multi.task_correct_count > single.task_correct_count
        or multi.valid_explanation_count > single.valid_explanation_count
        or multi.recovery_success_count > single.recovery_success_count
    )
    threshold_ok = _thresholds(multi)
    net = quality_benefit and security and threshold_ok
    evidence_arm = "real same-model A/B" if mode == "real" else "recorded A/B only"
    planner_decision: AdoptionDecision = "ADOPT" if net else "REJECT"
    planner_reason = (
        "Planner arm meets the approved recommended thresholds and improves at least one "
        "measured target."
        if net
        else "No approved real net-benefit evidence; keep the Planner experiment LAB_ONLY."
    )
    reviewer_decision: AdoptionDecision = "ADOPT" if net else "REJECT"
    reviewer_reason = (
        "Reviewer arm is eligible for /enhance only after the same-model A/B clears "
        "every threshold."
        if net
        else "Reviewer does not receive authorization from an ACCEPT; the A/B has not "
        "proved approved net benefit."
    )
    return (
        AdoptionCard(
            role="procurement_planner",
            decision=planner_decision,
            evidence_arm=evidence_arm,
            net_benefit=quality_benefit,
            thresholds_met=threshold_ok,
            security_passed=security,
            reason=planner_reason,
        ),
        AdoptionCard(
            role="policy_risk_reviewer",
            decision=reviewer_decision,
            evidence_arm=evidence_arm,
            net_benefit=quality_benefit,
            thresholds_met=threshold_ok,
            security_passed=security,
            reason=reviewer_reason,
        ),
        AdoptionCard(
            role="erp_coach",
            decision="RETAIN",
            evidence_arm="Phase 8 independent read-only Coach",
            net_benefit=False,
            thresholds_met=False,
            security_passed=True,
            reason=(
                "No child-Agent A/B was run; retain the independently verified Phase 8 "
                "read-only Coach entry."
            ),
        ),
        AdoptionCard(
            role="reconciliation_agent",
            decision="REJECT",
            evidence_arm=evidence_arm,
            net_benefit=False,
            thresholds_met=False,
            security_passed=security,
            reason=(
                "P9-12 remains an exception path; no evidence shows an Agent superior to "
                "deterministic reconciliation."
            ),
        ),
    )


def _fingerprint(report: ABReport | Mapping[str, object]) -> str:
    if isinstance(report, ABReport):
        body = report.model_dump(mode="json", exclude={"deterministic_fingerprint"})
    else:
        body = json.loads(canonical_json(report))
        body.pop("deterministic_fingerprint", None)
    for arm in ("single_agent", "planner_reviewer"):
        for item in body.get(arm, []):
            if isinstance(item, dict):
                item["elapsed_ms"] = 0
    for key in ("single_metrics", "multi_metrics"):
        metrics = body.get(key)
        if isinstance(metrics, dict):
            metrics["p50_latency_ms"] = 0
            metrics["p95_latency_ms"] = 0
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _scope(case: PatternCase) -> OrchestrationScope:
    seed = f"phase9-ab:{case.case_id}"
    task_id = uuid5(NAMESPACE_URL, f"{seed}:task")
    return OrchestrationScope(
        task_id=task_id,
        run_id=uuid5(NAMESPACE_URL, f"{seed}:run"),
        correlation_id=uuid5(NAMESPACE_URL, f"{seed}:correlation"),
        principal="synora-p9-buyer",
        company=case.plan.company,
        warehouse=case.plan.warehouse,
    )


async def _run_single_arm(
    baseline_cases: Sequence[BaselineCase],
    pattern_cases: Sequence[PatternCase],
    *,
    mode: ABMode,
    provider: Provider | None = None,
) -> tuple[ABCaseResult, ...]:
    results: list[ABCaseResult] = []
    for case, pattern_case in zip(baseline_cases, pattern_cases, strict=True):
        selected: Provider = provider if provider is not None else _RecordedSingleProvider(case)
        explanation, evidence = await enhance_plan(
            pattern_case.plan.model_dump(mode="json"),
            selected,
            provider_name="primary" if mode == "real" else "recorded-single-agent",
            context_environ={CONTEXT_INPUT_TOKEN_BUDGET_ENV: "100000"},
        )
        results.append(_single_result(case, pattern_case, explanation, evidence))
    return tuple(results)


async def _run_multi_arm(
    baseline_cases: Sequence[BaselineCase],
    pattern_cases: Sequence[PatternCase],
    *,
    mode: ABMode,
    provider: Provider | None = None,
) -> tuple[ABCaseResult, ...]:
    results: list[ABCaseResult] = []
    for case, pattern_case in zip(baseline_cases, pattern_cases, strict=True):
        selected: Provider = (
            provider if provider is not None else _RecordedMultiProvider(pattern_case)
        )
        orchestration = await run_planner_reviewer(
            pattern_case.plan.model_dump(mode="json"),
            selected,
            provider_name="primary" if mode == "real" else "recorded-planner-reviewer",
            scope=_scope(pattern_case),
        )
        results.append(_multi_result(case, pattern_case, orchestration))
    return tuple(results)


async def run_phase9_ab_async(
    *,
    case_spec_path: Path = BASELINE_CASE_SPEC_PATH,
    mode: ABMode = "recorded",
) -> ABReport:
    baseline_cases = load_phase9_baseline_cases(case_spec_path)
    pattern_cases = load_phase9_pattern_cases(case_spec_path)
    if mode == "recorded":
        single = await _run_single_arm(baseline_cases, pattern_cases, mode=mode)
        multi = await _run_multi_arm(baseline_cases, pattern_cases, mode=mode)
        model_name = "recorded-phase9"
    else:
        primary_single = provider_for_role("primary")
        try:
            single = await _run_single_arm(
                baseline_cases,
                pattern_cases,
                mode=mode,
                provider=primary_single,
            )
        finally:
            close = getattr(primary_single, "aclose", None)
            if callable(close):
                await close()
        primary_multi = provider_for_role("primary")
        try:
            multi = await _run_multi_arm(
                baseline_cases,
                pattern_cases,
                mode=mode,
                provider=primary_multi,
            )
        finally:
            close = getattr(primary_multi, "aclose", None)
            if callable(close):
                await close()
        model_name = os.getenv("OLLAMA_MODEL", "qwen3:8b")
    single_metrics = _metrics("single_agent", single)
    multi_metrics = _metrics("planner_reviewer", multi)
    cards = _adoption_cards(single_metrics, multi_metrics, mode=mode)
    security = all(item.security_pass for item in (*single, *multi)) and all(
        getattr(item, name) == 0
        for item in (*single, *multi)
        for name in (
            "unauthorized_tool_calls",
            "erp_business_writes",
            "scope_leaks",
            "secret_leaks",
        )
    )
    manifest = ABManifest(
        case_order=EXPECTED_CASE_ORDER,
        case_spec_sha256=case_spec_sha256(case_spec_path),
        code_head=_code_head(),
        provider_mode=mode,
        model_role="primary" if mode == "real" else "recorded",
        model_name=model_name,
    )
    status: Literal["PASS", "BLOCKED"] = (
        "PASS" if security and any(card.decision == "ADOPT" for card in cards) else "BLOCKED"
    )
    body = {
        "schema_version": "1",
        "manifest": manifest.model_dump(mode="json"),
        "single_agent": [item.model_dump(mode="json") for item in single],
        "planner_reviewer": [item.model_dump(mode="json") for item in multi],
        "single_metrics": single_metrics.model_dump(mode="json"),
        "multi_metrics": multi_metrics.model_dump(mode="json"),
        "adoption_cards": [item.model_dump(mode="json") for item in cards],
        "all_security_passed": security,
        "status": status,
    }
    fingerprint = _fingerprint(body)
    return ABReport(
        manifest=manifest,
        single_agent=single,
        planner_reviewer=multi,
        single_metrics=single_metrics,
        multi_metrics=multi_metrics,
        adoption_cards=cards,
        deterministic_fingerprint=fingerprint,
        all_security_passed=security,
        status=status,
    )


def run_phase9_ab(
    *, case_spec_path: Path = BASELINE_CASE_SPEC_PATH, mode: ABMode = "recorded"
) -> ABReport:
    return asyncio.run(run_phase9_ab_async(case_spec_path=case_spec_path, mode=mode))


def render_ab_decision_package(report: ABReport) -> str:
    single = report.single_metrics
    multi = report.multi_metrics
    lines = [
        "# Phase 9 P9.5 同模型 A/B 与 Adoption Cards",
        "",
        (
            f"状态：`{report.status}`；arm：`{report.manifest.provider_mode}`；"
            f"模型：`{report.manifest.model_role}/{report.manifest.model_name}`。"
        ),
        (
            f"code HEAD：`{report.manifest.code_head}`；"
            f"case SHA：`{report.manifest.case_spec_sha256}`。"
        ),
        f"deterministic fingerprint：`{report.deterministic_fingerprint}`。",
        "",
        (
            "两个 arm 按相同 P9-01→P9-12 顺序、同一源计划投影 digest 和同一模型角色执行；"
            "没有选择性重跑。"
        ),
        (
            "artifact 的 input_projection_digest 表示共享源计划；每个 case 的 arm_input_digest "
            "另行绑定实际发送给该 arm 的序列化 provider messages。"
        ),
        ("本地没有金额价格，token/延迟仅作成本代理；失败响应和完整 Prompt/候选原文不写入报告。"),
        "",
        (
            "| arm | task | valid | fallback | recovery | p50/p95 ms | total token | "
            "calls | security |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for metrics in (single, multi):
        lines.append(
            f"| `{metrics.arm}` | {metrics.task_correct_count}/{metrics.case_count} | "
            f"{metrics.valid_explanation_count}/{metrics.case_count} | "
            f"{metrics.safe_fallback_count}/{metrics.case_count} | "
            f"{metrics.recovery_success_count}/{metrics.case_count} | "
            f"{metrics.p50_latency_ms}/{metrics.p95_latency_ms} | {metrics.total_tokens} | "
            f"{metrics.model_calls_total} | "
            f"{metrics.unauthorized_tool_calls}/{metrics.erp_business_writes}/"
            f"{metrics.scope_leaks}/{metrics.secret_leaks} |"
        )
    lines.extend(
        [
            "",
            (
                "推荐门槛：task ≥7/12、valid ≥11/12、recovery ≥10/12、p95 ≤7833 ms、"
                "总 token ≤9653，安全项 100%，并至少改善一个获批目标。"
            ),
            "",
            "## Adoption Cards",
            "",
            "| role | decision | net benefit | thresholds | security | evidence |",
            "|---|---|---|---|---|---|",
        ]
    )
    for card in report.adoption_cards:
        lines.append(
            f"| `{card.role}` | `{card.decision}` | `{card.net_benefit}` | "
            f"`{card.thresholds_met}` | `{card.security_passed}` | {card.evidence_arm} |"
        )
    lines.extend(
        [
            "",
            "### 决策理由",
            "",
        ]
    )
    lines.extend(f"- `{card.role}`: {card.reason}" for card in report.adoption_cards)
    lines.extend(
        [
            "",
            (
                "未达标角色保留实验和拒绝理由；Reviewer 的 ACCEPT 仍不是安全授权。"
                "只有 Reviewer 卡达到门槛，才允许后续提交 `/enhance` 采用变更。"
            ),
        ]
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "BASELINE_CASE_SPEC_PATH",
    "EXPECTED_CASE_ORDER",
    "ABCaseResult",
    "ABManifest",
    "ABMetrics",
    "ABReport",
    "AdoptionCard",
    "render_ab_decision_package",
    "run_phase9_ab",
    "run_phase9_ab_async",
]
