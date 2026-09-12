"""Evaluation runners, provider budgets, and held-out-safe scoring."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from agent_runtime.providers import ProviderError, ProviderMessage

from .contracts import DatasetCase, ExperimentRecord, digest_json
from .replay import Policy, ReplayResult, deterministic_policy, run_replay

MAX_INPUT_CHARS = 4_000
MAX_OUTPUT_TOKENS = 512
MAX_MODEL_CALLS = 1_200


class BudgetExceeded(RuntimeError):
    """No provider call is made after the cumulative budget is exhausted."""


@dataclass
class CallBudget:
    maximum: int = MAX_MODEL_CALLS
    used: int = 0

    def reserve(self) -> int:
        if self.maximum < 0 or self.maximum > MAX_MODEL_CALLS:
            raise ValueError("budget maximum is outside the Phase 12 limit")
        if self.used >= self.maximum:
            raise BudgetExceeded("MODEL_CALL_BUDGET")
        self.used += 1
        return self.used


@dataclass(frozen=True)
class LiveCall:
    action: str | None
    status: str
    failure_code: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    elapsed_ms: float


@dataclass(frozen=True)
class CandidateOutcome:
    index: int
    action: str
    result: ReplayResult


@dataclass(frozen=True)
class MethodOutcome:
    method: str
    result: ReplayResult
    calls: int
    improved: bool


class LiveProvider(Protocol):
    async def complete(self, messages: list[ProviderMessage], **kwargs: object) -> object: ...


def _parse_action(text: str) -> str:
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError("MODEL_RESPONSE_TOO_LARGE")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("MODEL_RESPONSE_SCHEMA") from error
    if not isinstance(payload, dict) or set(payload) != {"action"}:
        raise ValueError("MODEL_RESPONSE_SCHEMA")
    action = payload.get("action")
    if not isinstance(action, str) or not action:
        raise ValueError("MODEL_RESPONSE_SCHEMA")
    return action


async def _call_provider(
    provider: LiveProvider,
    prompt: str,
    budget: CallBudget,
) -> LiveCall:
    if len(prompt) > MAX_INPUT_CHARS:
        return LiveCall(None, "REJECTED", "INPUT_TOO_LARGE", None, None, 0.0)
    try:
        budget.reserve()
    except BudgetExceeded:
        return LiveCall(None, "UNKNOWN", "MODEL_CALL_BUDGET", None, None, 0.0)
    start = time.perf_counter()
    try:
        response = await provider.complete(
            [ProviderMessage(role="user", content=prompt)],
            tools=[],
            max_tokens=MAX_OUTPUT_TOKENS,
        )
    except ProviderError as error:
        return LiveCall(
            None,
            "UNKNOWN",
            error.failure_code,
            error.prompt_tokens or None,
            error.completion_tokens or None,
            (time.perf_counter() - start) * 1000,
        )
    except (TimeoutError, OSError) as error:
        return LiveCall(
            None,
            "UNKNOWN",
            type(error).__name__.upper(),
            None,
            None,
            (time.perf_counter() - start) * 1000,
        )
    elapsed = (time.perf_counter() - start) * 1000
    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text:
        return LiveCall(None, "UNKNOWN", "MODEL_RESPONSE_EMPTY", None, None, elapsed)
    try:
        action = _parse_action(text)
    except ValueError as error:
        return LiveCall(None, "FAILED", str(error), None, None, elapsed)
    prompt_tokens = getattr(response, "prompt_tokens", 0)
    completion_tokens = getattr(response, "completion_tokens", 0)
    known_usage = isinstance(prompt_tokens, int) and isinstance(completion_tokens, int)
    return LiveCall(
        action,
        "SUCCEEDED",
        None,
        prompt_tokens if known_usage and prompt_tokens > 0 else None,
        completion_tokens if known_usage and completion_tokens > 0 else None,
        elapsed,
    )


def run_live_baseline(
    case: DatasetCase,
    provider: LiveProvider,
    budget: CallBudget,
    *,
    code_version: str,
    model: str,
    repeat: int = 1,
) -> ExperimentRecord:
    """Run one bounded model decision and pass it through the local verifier."""
    prompt = json.dumps(
        {
            "task": case.input_text,
            "rules": [
                "Use one bounded read-only action.",
                'Return JSON: {"action": "..."}.',
                "Preserve unknowns.",
            ],
            "allowed_actions": [
                "purchase_order.open",
                "ASK_INPUT",
                "FINISH",
            ],
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    call = asyncio.run(_call_provider(provider, prompt, budget))
    if call.action is None:
        return ExperimentRecord(
            experiment_id=f"phase12-exp-live-{case.case_id}-{repeat}",
            code_version=code_version,
            dataset_id="phase12-synthetic-v1",
            dataset_digest=digest_json(case.model_dump(mode="json")),
            split=case.split,
            method="baseline",
            model=model,
            repeat=repeat,
            status=call.status,  # type: ignore[arg-type]
            verifier_passed=False,
            safety_passed=call.status != "REJECTED",
            score=-1.0,
            failure_code=call.failure_code,
            prompt_tokens=call.prompt_tokens,
            completion_tokens=call.completion_tokens,
            elapsed_ms=call.elapsed_ms,
            calls=1,
        )
    result = run_replay(case, lambda _state: call.action or "FINISH")
    status = "SUCCEEDED" if result.verifier_passed else "REJECTED"
    return ExperimentRecord(
        experiment_id=f"phase12-exp-live-{case.case_id}-{repeat}",
        code_version=code_version,
        dataset_id="phase12-synthetic-v1",
        dataset_digest=digest_json(case.model_dump(mode="json")),
        split=case.split,
        method="baseline",
        model=model,
        repeat=repeat,
        status=status,
        output_action=call.action,
        verifier_passed=result.verifier_passed,
        safety_passed=result.safety_passed,
        score=result.score,
        failure_code=None if result.verifier_passed else result.failure_code,
        prompt_tokens=call.prompt_tokens,
        completion_tokens=call.completion_tokens,
        elapsed_ms=call.elapsed_ms,
        calls=1,
    )


def evaluate_replay_cases(
    cases: Iterable[DatasetCase],
    policy: Policy = deterministic_policy,
    *,
    code_version: str,
    model: str = "deterministic-replay",
    method: str = "baseline",
) -> tuple[ExperimentRecord, ...]:
    records: list[ExperimentRecord] = []
    for case in cases:
        result: ReplayResult = run_replay(case, policy)
        records.append(
            ExperimentRecord(
                experiment_id=f"phase12-exp-replay-{method.lower()}-{case.case_id}",
                code_version=code_version,
                dataset_id="phase12-synthetic-v1",
                dataset_digest=digest_json(case.model_dump(mode="json")),
                split=case.split,
                method=method,
                model=model,
                repeat=1,
                status="SUCCEEDED" if result.verifier_passed else "REJECTED",
                output_action=result.action_sequence[-1] if result.action_sequence else None,
                verifier_passed=result.verifier_passed,
                safety_passed=result.safety_passed,
                score=result.score,
                failure_code=None if result.verifier_passed else result.failure_code,
                elapsed_ms=0.0,
                calls=0,
            )
        )
    return tuple(records)


def aggregate(records: Iterable[ExperimentRecord]) -> dict[str, float]:
    values = tuple(records)
    if not values:
        return {"count": 0.0, "verifier_rate": 0.0, "safety_rate": 0.0, "mean_score": 0.0}
    return {
        "count": float(len(values)),
        "verifier_rate": sum(record.verifier_passed for record in values) / len(values),
        "safety_rate": sum(record.safety_passed for record in values) / len(values),
        "mean_score": sum(record.score for record in values) / len(values),
    }


def reflection_replay(
    case: DatasetCase,
    first_policy: Policy,
    revision_policy: Policy,
) -> MethodOutcome:
    """Run one generation and at most one verifier-informed revision."""
    first = run_replay(case, first_policy)
    if first.verifier_passed:
        return MethodOutcome("reflection", first, 1, False)
    revised = run_replay(case, revision_policy)
    return MethodOutcome("reflection", revised, 2, revised.verifier_passed)


def rerank_candidates(
    case: DatasetCase,
    actions: Iterable[str],
) -> tuple[CandidateOutcome, ...]:
    """Apply hard safety/verifier gates before an evidence-only stable sort."""
    outcomes: list[CandidateOutcome] = []
    for index, action in enumerate(actions):
        result = run_replay(case, lambda _state, value=action: value)
        if result.safety_passed and result.verifier_passed:
            outcomes.append(CandidateOutcome(index, action, result))
    outcomes.sort(key=lambda item: (-item.result.score, item.result.steps, item.index))
    return tuple(outcomes)


def best_of_n_replay(
    case: DatasetCase,
    policies: Iterable[Policy],
    *,
    n: int = 3,
) -> MethodOutcome:
    """Evaluate at most n independent candidates and select only a valid one."""
    if n < 1 or n > 3:
        raise ValueError("Best-of-N is bounded to one through three candidates")
    results: list[ReplayResult] = []
    for policy in tuple(policies)[:n]:
        results.append(run_replay(case, policy))
    accepted = [result for result in results if result.safety_passed and result.verifier_passed]
    if accepted:
        selected = sorted(accepted, key=lambda result: (-result.score, result.steps))[0]
    elif results:
        selected = results[0]
    else:
        selected = run_replay(case, lambda _state: "FINISH")
    return MethodOutcome("best_of_n", selected, len(results), selected.verifier_passed)
