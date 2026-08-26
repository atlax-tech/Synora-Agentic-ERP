"""P4.1 strict contracts, canonical keys, digests, and trace redaction."""

from uuid import UUID

import pytest
from agent_runtime.agent.contracts import (
    Action,
    BudgetSnapshot,
    FinalAnswer,
    Observation,
    StopReason,
    TraceRecorder,
    canonical_json,
    observation_from_summary,
    validate_action_tool,
)
from pydantic import ValidationError

RUN_ID = UUID("37e1d8a5-1730-4ad0-bffd-217774ed9fab")
CORRELATION_ID = UUID("1687c82a-4b61-4e6e-855a-a10ec3578b96")


def _action(**changes: object) -> Action:
    values: dict[str, object] = {
        "step": 1,
        "tool_name": "item.lookup",
        "canonical_args": {"query": "bearing", "offset": 0, "limit": 20},
        "correlation_id": CORRELATION_ID,
    }
    values.update(changes)
    return Action.model_validate(values)


def test_action_rejects_unknown_fields_and_non_finite_json() -> None:
    with pytest.raises(ValidationError):
        _action(unexpected="nope")
    with pytest.raises(ValidationError):
        _action(canonical_args={"query": float("nan")})


def test_canonical_call_key_is_stable_and_validates_existing_tool_schema() -> None:
    first = _action(canonical_args={"limit": 20, "query": "bearing", "offset": 0})
    second = _action(canonical_args={"offset": 0, "query": "bearing", "limit": 20})
    assert first.call_key() == second.call_key()
    assert validate_action_tool(first).name == "item.lookup"

    with pytest.raises(ValidationError):
        validate_action_tool(_action(canonical_args={"query": "bearing", "unexpected": True}))


def test_observation_digest_is_sha256_of_bounded_summary() -> None:
    observation = observation_from_summary(
        run_id=RUN_ID,
        step=1,
        tool_name="item.lookup",
        ok=True,
        summary="1 item found",
    )
    assert len(observation.digest) == 64
    assert observation.summary == "1 item found"


def test_observation_rejects_digest_that_does_not_match_summary() -> None:
    with pytest.raises(ValidationError):
        Observation(
            step=1,
            tool_name="item.lookup",
            ok=True,
            summary="1 item found",
            digest="0" * 64,
        )


def test_trace_recorder_orders_events_and_redacts_sensitive_payload() -> None:
    capability = "A" * 43
    recorder = TraceRecorder(RUN_ID, secret_values=frozenset({capability}))
    first = recorder.add(
        "action.proposed",
        {
            "tool_name": "item.lookup",
            "capability": capability,
            "prompt": "do not persist this",
            "nested": {"api_key": "secret", "value": capability},
        },
    )
    second = recorder.add("guard.checked", {"allowed": True})
    assert first.sequence == 1
    assert second.sequence == 2
    assert first.payload["capability"] == "[REDACTED]"
    assert first.payload["prompt"] == "[REDACTED]"
    assert first.payload["nested"] == {"api_key": "[REDACTED]", "value": "[REDACTED]"}
    assert capability not in repr(recorder.events())


def test_final_answer_requires_stop_reason_for_final_answer_stop() -> None:
    reason = StopReason(code="FINAL_ANSWER", step=1, budget_snapshot=BudgetSnapshot())
    answer = FinalAnswer(
        status="SUCCEEDED",
        summary="done",
        evidence_refs=("a" * 64,),
        stop_reason=reason,
    )
    assert answer.status == "SUCCEEDED"


def test_final_answer_rejects_non_digest_evidence_reference() -> None:
    with pytest.raises(ValidationError):
        FinalAnswer(status="SUCCEEDED", summary="done", evidence_refs=("abc",))


def test_trace_recorder_redacts_secret_like_text_and_bounds_nested_payload() -> None:
    recorder = TraceRecorder(RUN_ID)
    event = recorder.add(
        "run.started",
        {
            "detail": "capability=do-not-persist",
            "nested": [[[[["too deep"]]]]],
            "long": "x" * 5_000,
        },
    )
    assert event.payload["detail"] == "[REDACTED]"
    assert event.payload["nested"] == [[[["[TRUNCATED]"]]]]
    assert len(event.payload["long"]) == 4_000


def test_canonical_json_is_strict_about_non_standard_numbers() -> None:
    with pytest.raises(ValueError):
        canonical_json({"value": float("inf")})
