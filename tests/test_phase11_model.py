from __future__ import annotations

import json

import httpx
import pytest
from agent_runtime.providers import DeterministicProvider, ProviderResponse

from labs.web_gui.benchmark import _lab_server
from labs.web_gui.browser import run_model_dom_task
from labs.web_gui.contracts import Observation, TaskSpec, TrialBudget
from labs.web_gui.model import (
    LiveTextModel,
    LiveVisionModel,
    ModelCallError,
    ModelDecision,
    ModelResponse,
    decision_from_model,
    decision_from_vision,
    parse_model_decision,
)


def _observation() -> Observation:
    return Observation(
        page_version="synthetic-procurement-v1",
        source="synthetic",
        mode="dom",
        content='{"targets":["search-input"],"text":"Find a purchase order"}',
    )


def _wire(action_type: str, **values: object) -> str:
    return json.dumps({"action_type": action_type, **values})


def test_model_decision_rejects_unknown_wire_fields() -> None:
    with pytest.raises(ModelCallError, match="MODEL_RESPONSE_SCHEMA"):
        parse_model_decision(
            {"action_type": "finish", "fields": {}, "script": "alert(1)"},
            _observation(),
        )


def test_model_decision_accepts_observed_answer_envelope_only() -> None:
    decision = parse_model_decision(
        {
            "answer": {
                "action_type": "finish",
                "fields": {"purchase_order": "PUR-ORD-0001"},
            }
        },
        _observation(),
    )

    assert decision.proposal.action_type == "finish"
    assert decision.fields is not None
    assert decision.fields["purchase_order"] == "PUR-ORD-0001"

    with pytest.raises(ModelCallError, match="MODEL_RESPONSE_SCHEMA"):
        parse_model_decision(
            {
                "answer": {"action_type": "finish", "fields": {}},
                "unexpected": True,
            },
            _observation(),
        )


def test_live_text_model_preserves_text_only_observation_contract() -> None:
    seen: list[str] = []

    def factory() -> DeterministicProvider:
        return DeterministicProvider(
            scripted_responses=[
                ProviderResponse(
                    text=_wire("search", target_ref="search-input", text="PUR-ORD-0001"),
                    prompt_tokens=3,
                    completion_tokens=4,
                )
            ]
        )

    client = LiveTextModel(
        environ={"ASSIST_MODEL": "text-test"},
        provider_factory=factory,
    )
    original_call = client.call

    def recording_call(prompt: str, *, max_tokens: int = 1024) -> ModelResponse:
        seen.append(prompt)
        return original_call(prompt, max_tokens=max_tokens)

    client.call = recording_call  # type: ignore[method-assign]
    spec = TaskSpec(case_id="model-contract", purchase_order="PUR-ORD-0001", mode="dom")
    decision = decision_from_model(client, spec, _observation(), 12)

    assert decision.proposal.action_type == "search"
    assert decision.model == "text-test"
    assert "PUR-ORD-0001" in seen[0]
    assert "Supplier A" not in seen[0]


def test_structured_decision_forwards_output_budget() -> None:
    seen: list[int] = []

    def factory() -> DeterministicProvider:
        return DeterministicProvider(
            scripted_responses=[ProviderResponse(text=_wire("finish", fields={}))]
        )

    client = LiveTextModel(environ={"ASSIST_MODEL": "text-test"}, provider_factory=factory)
    original_call = client.call

    def recording_call(prompt: str, *, max_tokens: int = 1024) -> ModelResponse:
        seen.append(max_tokens)
        return original_call(prompt, max_tokens=max_tokens)

    client.call = recording_call  # type: ignore[method-assign]
    spec = TaskSpec(
        case_id="model-budget",
        purchase_order="PUR-ORD-0001",
        mode="dom",
        budget=TrialBudget(max_output_tokens=7),
    )

    decision_from_model(client, spec, _observation(), 12)

    assert seen == [7]


def test_model_dom_task_executes_model_selected_actions_and_records_usage() -> None:
    responses = [
        _wire("search", target_ref="search-input", text="PUR-ORD-0001"),
        _wire("click", target_ref="order:PUR-ORD-0001"),
        _wire(
            "finish",
            fields={
                "purchase_order": "PUR-ORD-0001",
                "supplier": "Supplier A",
                "status": "To Receive and Bill",
                "currency": "CNY",
            },
        ),
    ]
    queue = list(responses)

    def factory() -> DeterministicProvider:
        return DeterministicProvider(
            scripted_responses=[
                ProviderResponse(text=queue.pop(0), prompt_tokens=2, completion_tokens=3)
            ]
        )

    client = LiveTextModel(environ={"ASSIST_MODEL": "text-test"}, provider_factory=factory)

    def decider(observation: Observation, spec: TaskSpec, remaining: int) -> ModelDecision:
        return decision_from_model(client, spec, observation, remaining)

    with _lab_server() as base_url:
        run = run_model_dom_task(
            base_url,
            TaskSpec(case_id="model-dom-success", purchase_order="PUR-ORD-0001", mode="dom"),
            decider,
        )

    assert run.result.status == "SUCCEEDED"
    assert run.result.fields["currency"] == "CNY"
    assert run.model_calls == 3
    assert run.prompt_tokens == 6
    assert run.completion_tokens == 9
    assert all(receipt.after_observation_id is not None for receipt in run.result.actions[:2])


def test_model_dom_task_rejects_unobserved_target() -> None:
    client = LiveTextModel(
        environ={"ASSIST_MODEL": "text-test"},
        provider_factory=lambda: DeterministicProvider(
            scripted_responses=[ProviderResponse(text=_wire("click", target_ref="order:FORGED"))]
        ),
    )

    def decider(observation: Observation, spec: TaskSpec, remaining: int) -> ModelDecision:
        return decision_from_model(client, spec, observation, remaining)

    with _lab_server() as base_url:
        run = run_model_dom_task(
            base_url,
            TaskSpec(case_id="model-dom-forged", purchase_order="PUR-ORD-0001", mode="dom"),
            decider,
        )

    assert run.result.status == "FAILED"
    assert run.result.stop_reason == "ACTION_REJECTED"
    assert run.result.actions[0].result == "REJECTED"


def test_vision_decision_adapter_keeps_image_result_untrusted() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"action_type":"finish","fields":{}}'}}]},
        )

    client = LiveVisionModel(
        "assist",
        environ={
            "ASSIST_BASE_URL": "https://vision.example/v1",
            "ASSIST_API_KEY": "secret",
            "ASSIST_MODEL": "vision-test",
        },
        transport=httpx.MockTransport(handler),
    )
    decision = decision_from_vision(
        client,
        TaskSpec(case_id="vision-model", purchase_order="PUR-ORD-0001", mode="vision"),
        Observation(
            page_version="screenshot:test",
            source="synthetic",
            mode="vision",
            content='{"screenshot_sha256":"' + "0" * 64 + '"}',
            screenshot_sha256="0" * 64,
            viewport_width=100,
            viewport_height=100,
        ),
        b"\x89PNG\r\n\x1a\nsynthetic",
        12,
    )

    assert decision.proposal.action_type == "finish"
    assert decision.model == "vision-test"
