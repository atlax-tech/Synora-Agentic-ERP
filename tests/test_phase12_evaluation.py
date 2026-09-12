from __future__ import annotations

from agent_runtime.providers import ProviderResponse

from labs.self_improvement.data import build_synthetic_manifest
from labs.self_improvement.evaluation import (
    CallBudget,
    aggregate,
    evaluate_replay_cases,
    run_live_baseline,
)


class FakeProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def complete(self, messages: list[object], **kwargs: object) -> ProviderResponse:
        del messages, kwargs
        self.calls += 1
        return ProviderResponse(text=self.text, prompt_tokens=4, completion_tokens=2)


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
