from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from agent_runtime.providers import ProviderError, ProviderMessage, ProviderResponse

from labs.self_improvement.data import build_synthetic_manifest
from labs.self_improvement.evaluation import (
    CallBudget,
    ReservationLedger,
    _call_provider,
    aggregate,
    build_live_prompt,
    evaluate_replay_cases,
    rerank_candidates,
    run_live_baseline,
    run_live_baselines,
    run_live_methods,
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


class CancelledProvider:
    async def complete(self, messages: list[ProviderMessage], **kwargs: object) -> ProviderResponse:
        del messages, kwargs
        raise asyncio.CancelledError


class SequenceProvider:
    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.prompts: list[str] = []
        self.kwargs: list[dict[str, object]] = []

    async def complete(self, messages: list[ProviderMessage], **kwargs: object) -> ProviderResponse:
        self.prompts.append(messages[0].content)
        self.kwargs.append(dict(kwargs))
        text = self.texts.pop(0) if self.texts else '{"action":"FINISH"}'
        return ProviderResponse(text=text, prompt_tokens=4, completion_tokens=2)


def test_replay_evaluation_aggregates_verifier_and_safety() -> None:
    manifest = build_synthetic_manifest("eval-test")
    records = evaluate_replay_cases(manifest.cases[:6], code_version="eval-test")
    summary = aggregate(records)
    assert summary["count"] == 6
    assert summary["verifier_rate"] == 1.0
    assert summary["safety_rate"] == 1.0


def test_candidate_replay_requires_content_and_boundary_digests() -> None:
    manifest = build_synthetic_manifest("eval-test")
    with pytest.raises(ValueError, match="candidate experiments require"):
        evaluate_replay_cases(
            manifest.cases[:1],
            code_version="eval-test",
            method="prompt-candidate",
            candidate_id="phase12-prompt-0000000000000000",
        )


def test_live_baseline_reserves_budget_and_verifies_action() -> None:
    case = next(
        case for case in build_synthetic_manifest("eval-test").cases if case.kind == "MISSING_INPUT"
    )
    provider = FakeProvider('{"action":"ASK_INPUT"}')
    budget = CallBudget(maximum=1)
    record = run_live_baseline(case, provider, budget, code_version="eval-test", model="fake")
    assert record.verifier_passed
    assert record.prompt_tokens == 4
    assert record.response_sha256 is not None
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


def test_budget_rejects_invalid_initial_state() -> None:
    with pytest.raises(ValueError, match="maximum"):
        CallBudget(maximum=1_201)
    with pytest.raises(ValueError, match="used"):
        CallBudget(maximum=1, used=2)


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


def test_reservation_ledger_survives_restart_and_blocks_reuse(tmp_path: Path) -> None:
    ledger = ReservationLedger(tmp_path / "reservations.jsonl")
    budget = CallBudget(maximum=2, ledger=ledger, batch_id="batch-a")
    assert budget.reserve("repeat:1:case:case-a") == 1
    restarted = CallBudget(
        maximum=2,
        ledger=ReservationLedger(tmp_path / "reservations.jsonl"),
        batch_id="batch-a",
    )
    assert restarted.used == 1
    provider = FakeProvider('{"action":"FINISH"}')
    call = asyncio.run(
        _call_provider(
            provider,
            '{"task":"x"}',
            restarted,
            reservation_key="repeat:1:case:case-a",
        )
    )
    assert call.failure_code == "CALL_RESERVATION_EXISTS"
    assert not call.attempted
    assert provider.calls == 0


def test_recorded_reservations_are_not_counted_twice_after_restart(tmp_path: Path) -> None:
    ledger = ReservationLedger(tmp_path / "reservations.jsonl")
    budget = CallBudget(maximum=2, ledger=ledger, batch_id="batch-a")
    provider = FakeProvider('{"action":"FINISH"}')
    call = asyncio.run(
        _call_provider(
            provider,
            '{"task":"x"}',
            budget,
            reservation_key="repeat:1:case:case-a",
        )
    )
    assert call.attempted
    ledger.mark_recorded(("repeat:1:case:case-a",))
    restarted = CallBudget(
        maximum=2,
        ledger=ReservationLedger(tmp_path / "reservations.jsonl"),
        batch_id="batch-b",
    )
    assert restarted.used == 0


def test_reservation_states_expose_key_and_state(tmp_path: Path) -> None:
    ledger = ReservationLedger(tmp_path / "reservations.jsonl")
    budget = CallBudget(maximum=1, ledger=ledger, batch_id="batch-a")
    assert budget.reserve("repeat:1:case:case-a") == 1
    assert ledger.states() == (("repeat:1:case:case-a", "RESERVED"),)


def test_stale_reserved_is_reconciled_as_unknown_and_keeps_budget(tmp_path: Path) -> None:
    path = tmp_path / "reservations.jsonl"
    ledger = ReservationLedger(path)
    budget = CallBudget(maximum=2, ledger=ledger, batch_id="batch-a")
    budget.reserve("batch-a:repeat:1:case:case-a")

    restarted = ReservationLedger(path)
    assert restarted.reconcile_reserved() == ("batch-a:repeat:1:case:case-a",)
    assert restarted.states() == (("batch-a:repeat:1:case:case-a", "UNKNOWN"),)
    assert CallBudget(maximum=2, ledger=ReservationLedger(path), batch_id="batch-b").used == 1


def test_cancelled_provider_call_is_recorded_unknown(tmp_path: Path) -> None:
    path = tmp_path / "reservations.jsonl"
    ledger = ReservationLedger(path)
    budget = CallBudget(maximum=1, ledger=ledger, batch_id="batch-a")
    call = asyncio.run(
        _call_provider(
            CancelledProvider(),
            '{"task":"x"}',
            budget,
            reservation_key="batch-a:repeat:1:case:case-a",
        )
    )
    assert call.status == "UNKNOWN"
    assert call.failure_code == "MODEL_CALL_CANCELLED"
    assert call.attempted
    assert ledger.states() == (("batch-a:repeat:1:case:case-a", "UNKNOWN"),)


def test_new_batch_can_reserve_the_same_case_after_a_prior_batch(tmp_path: Path) -> None:
    path = tmp_path / "reservations.jsonl"
    provider = FakeProvider('{"action":"FINISH"}')
    first = CallBudget(
        maximum=3,
        ledger=ReservationLedger(path),
        batch_id="batch-a",
    )
    asyncio.run(
        _call_provider(
            provider,
            '{"task":"x"}',
            first,
            reservation_key="batch-a:repeat:1:case:case-a",
        )
    )
    ReservationLedger(path).mark_recorded_matching(("batch-a:repeat:1:case:case-a",))
    second = CallBudget(
        maximum=3,
        ledger=ReservationLedger(path),
        batch_id="batch-b",
    )
    call = asyncio.run(
        _call_provider(
            provider,
            '{"task":"x"}',
            second,
            reservation_key="batch-b:repeat:1:case:case-a",
        )
    )
    assert call.attempted
    assert provider.calls == 2


def test_recorded_matching_never_consumes_a_different_batch(tmp_path: Path) -> None:
    path = tmp_path / "reservations.jsonl"
    ledger = ReservationLedger(path)
    first = CallBudget(maximum=4, ledger=ledger, batch_id="batch-a")
    second = CallBudget(maximum=4, ledger=ledger, batch_id="batch-b")
    first.reserve("batch-a:repeat:1:case:case-a")
    ledger.finish("batch-a:repeat:1:case:case-a", "UNKNOWN")
    second.reserve("batch-b:repeat:1:case:case-a")
    ledger.finish("batch-b:repeat:1:case:case-a", "UNKNOWN")

    ledger.mark_recorded_matching(("batch-a:repeat:1:case:case-a",))

    assert ledger.states() == (
        ("batch-a:repeat:1:case:case-a", "RECORDED"),
        ("batch-b:repeat:1:case:case-a", "UNKNOWN"),
    )


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


def test_live_prompt_includes_candidate_without_scoring_labels() -> None:
    case = next(case for case in build_synthetic_manifest("eval-test").cases if case.split == "dev")
    prompt = build_live_prompt(case, "prompt-candidate", candidate_content="preserve unknowns")
    assert "preserve unknowns" in prompt
    assert case.kind not in prompt
    assert "expected_action" not in prompt
    assert "expected_status" not in prompt


def test_live_request_uses_bounded_reasoning_envelope_and_visible_output_cap() -> None:
    case = next(case for case in build_synthetic_manifest("eval-test").cases if case.split == "dev")
    provider = SequenceProvider('{"action":"ASK_INPUT"}')
    budget = CallBudget(maximum=1, batch_id="envelope-batch")
    run_live_methods(
        (case,),
        provider,
        budget,
        method="baseline",
        code_version="eval-test",
        model="fake",
        dataset_id="phase12-synthetic-v2",
        dataset_digest=build_synthetic_manifest("eval-test").dataset_digest,
    )
    assert provider.kwargs[0]["max_tokens"] == 2048
    assert provider.kwargs[0]["response_format"] == "json_object"
    assert provider.kwargs[0]["reasoning_effort"] == "none"


def test_live_reflection_uses_one_observable_revision() -> None:
    case = next(
        case for case in build_synthetic_manifest("eval-test").cases if case.kind == "COMPLETE_READ"
    )
    provider = SequenceProvider(
        '{"action":"FINISH"}', '{"actions":["purchase_order.open","FINISH"]}'
    )
    budget = CallBudget(maximum=2, batch_id="reflection-batch")
    record = run_live_methods(
        (case,),
        provider,
        budget,
        method="reflection",
        code_version="eval-test",
        model="fake",
        dataset_id="phase12-synthetic-v2",
        dataset_digest=build_synthetic_manifest("eval-test").dataset_digest,
    )[0]
    assert record.verifier_passed
    assert record.calls == 2
    assert len(record.reservation_keys) == 2
    assert len(provider.prompts) == 2


def test_live_best_of_three_reranks_only_observable_candidates() -> None:
    case = next(
        case for case in build_synthetic_manifest("eval-test").cases if case.kind == "COMPLETE_READ"
    )
    provider = SequenceProvider(
        '{"action":"FINISH"}',
        '{"actions":["purchase_order.open","FINISH"]}',
        '{"action":"ASK_INPUT"}',
    )
    budget = CallBudget(maximum=3, batch_id="best-batch")
    record = run_live_methods(
        (case,),
        provider,
        budget,
        method="best-of-3",
        code_version="eval-test",
        model="fake",
        dataset_id="phase12-synthetic-v2",
        dataset_digest=build_synthetic_manifest("eval-test").dataset_digest,
    )[0]
    assert record.verifier_passed
    assert record.calls == 3
    assert len(record.reservation_keys) == 3
    assert len(provider.prompts) == 3


def test_reranker_uses_observable_gates_before_oracle_scoring() -> None:
    case = next(
        case for case in build_synthetic_manifest("eval-test").cases if case.kind == "COMPLETE_READ"
    )
    outcomes = rerank_candidates(case, ("ASK_INPUT", "FINISH"))
    assert [outcome.action for outcome in outcomes] == ["ASK_INPUT"]
    assert not outcomes[0].result.verifier_passed
