from __future__ import annotations

import asyncio
import time

import pytest
from agent_runtime.providers import ProviderError, ProviderMessage, ProviderResponse

from labs.self_improvement.data import build_synthetic_manifest
from labs.self_improvement.evaluation import (
    CallBudget,
    _call_provider,
    aggregate,
    evaluate_replay_cases,
    rerank_candidates,
    run_live_baseline,
    run_live_baselines,
)


class FakeProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def complete(self, messages: list[ProviderMessage], **kwargs: object) -> ProviderResponse:
        del messages, kwargs
        self.calls += 1
        return ProviderResponse(text=self.text, prompt_tokens=4, completion_tokens=2)


class FailingProvider:
    def __init__(self, failure_code: str) -> None:
        self.failure_code = failure_code
        self.calls = 0

    async def complete(self, messages: list[ProviderMessage], **kwargs: object) -> ProviderResponse:
        del messages, kwargs
        self.calls += 1
        raise ProviderError("blocked", failure_code=self.failure_code)


class SlowProvider:
    async def complete(self, messages: list[ProviderMessage], **kwargs: object) -> ProviderResponse:
        del messages, kwargs
        await asyncio.sleep(0.01)
        return ProviderResponse(text='{"action":"FINISH"}')


def test_replay_evaluation_aggregates_verifier_and_safety() -> None:
    manifest = build_synthetic_manifest("eval-test")
    records = evaluate_replay_cases(manifest.cases[:6], code_version="eval-test")
    summary = aggregate(records)
    assert summary["count"] == 6
    assert summary["verifier_rate"] == 1.0
    assert summary["safety_rate"] == 1.0


def test_live_baseline_reserves_budget_and_verifies_action() -> None:
    case = next(
        case for case in build_synthetic_manifest("eval-test").cases if case.kind == "MISSING_INPUT"
    )
    provider = FakeProvider('{"action":"ASK_INPUT"}')
    budget = CallBudget(maximum=1)
    record = run_live_baseline(case, provider, budget, code_version="eval-test", model="fake")
    assert record.verifier_passed
    assert record.prompt_tokens == 4
    assert budget.used == 1
    assert provider.calls == 1


def test_live_baseline_does_not_call_after_budget_exhaustion() -> None:
    case = next(
        case for case in build_synthetic_manifest("eval-test").cases if case.kind == "MISSING_INPUT"
    )
    provider = FakeProvider('{"action":"ASK_INPUT"}')
    budget = CallBudget(maximum=0)
    record = run_live_baseline(case, provider, budget, code_version="eval-test", model="fake")
    assert record.status == "UNKNOWN"
    assert record.failure_code == "MODEL_CALL_BUDGET"
    assert record.calls == 0
    assert provider.calls == 0


def test_live_invalid_json_is_recorded_without_retry() -> None:
    case = next(
        case for case in build_synthetic_manifest("eval-test").cases if case.kind == "MISSING_INPUT"
    )
    provider = FakeProvider("not json")
    budget = CallBudget(maximum=2)
    record = run_live_baseline(case, provider, budget, code_version="eval-test", model="fake")
    assert record.status == "FAILED"
    assert record.failure_code == "MODEL_RESPONSE_SCHEMA"
    assert budget.used == 1
    assert provider.calls == 1


def test_three_blocking_failures_stop_the_batch() -> None:
    case = next(
        case for case in build_synthetic_manifest("eval-test").cases if case.kind == "MISSING_INPUT"
    )
    provider = FailingProvider("TRANSPORT_ERROR")
    budget = CallBudget(maximum=10)
    for _ in range(3):
        run_live_baseline(case, provider, budget, code_version="eval-test", model="fake")
    stopped = run_live_baseline(case, provider, budget, code_version="eval-test", model="fake")
    assert stopped.failure_code == "MODEL_BATCH_BLOCKED"
    assert stopped.calls == 0
    assert provider.calls == 3
    assert budget.used == 3


def test_provider_call_wall_clock_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    import labs.self_improvement.evaluation as evaluation

    monkeypatch.setattr(evaluation, "MAX_CALL_SECONDS", 0.001)
    start = time.perf_counter()
    call = asyncio.run(_call_provider(SlowProvider(), '{"task":"x"}', CallBudget(maximum=1)))
    assert call.failure_code == "MODEL_CALL_TIMEOUT"
    assert call.attempted
    assert time.perf_counter() - start < 1.0


def test_live_batch_reuses_one_event_loop_for_provider_client() -> None:
    manifest = build_synthetic_manifest("eval-test")
    cases = tuple(case for case in manifest.cases if case.kind == "MISSING_INPUT")[:2]
    provider = FakeProvider('{"action":"ASK_INPUT"}')
    budget = CallBudget(maximum=10)
    records = run_live_baselines(
        cases,
        provider,
        budget,
        code_version="eval-test",
        model="fake",
        repeats=2,
        dataset_id=manifest.dataset_id,
        dataset_digest=manifest.dataset_digest,
    )
    assert len(records) == 4
    assert provider.calls == 4
    assert all(record.calls == 1 for record in records)


def test_reranker_uses_observable_gates_before_oracle_scoring() -> None:
    case = next(
        case for case in build_synthetic_manifest("eval-test").cases if case.kind == "COMPLETE_READ"
    )
    outcomes = rerank_candidates(case, ("ASK_INPUT", "FINISH"))
    assert [outcome.action for outcome in outcomes] == ["ASK_INPUT"]
    assert not outcomes[0].result.verifier_passed
