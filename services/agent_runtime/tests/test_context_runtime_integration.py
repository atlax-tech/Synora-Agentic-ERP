"""Phase 7.2b Runtime context, trace ordering, and posterior budget tests."""

import asyncio
import json
from uuid import UUID

from agent_runtime.agent.context import CONTEXT_INPUT_TOKEN_BUDGET_ENV
from agent_runtime.agent.contracts import Action, Observation, observation_from_summary
from agent_runtime.agent.native_tool_calling import NativeToolCallingLimits, run_native_tool_calling
from agent_runtime.providers import ProviderResponse, ProviderToolCall

RUN_ID = UUID("37e1d8a5-1730-4ad0-bffd-217774ed9fab")
CORRELATION_ID = UUID("1687c82a-4b61-4e6e-855a-a10ec3578b96")


class _Provider:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self.responses = responses
        self.calls = 0

    async def complete(self, *args: object, **kwargs: object) -> ProviderResponse:
        del args, kwargs
        self.calls += 1
        return self.responses.pop(0)


class _Adapter:
    def __init__(self, summaries: dict[str, str]) -> None:
        self.summaries = summaries
        self.calls: list[Action] = []

    async def execute(self, action: Action) -> Observation:
        self.calls.append(action)
        return observation_from_summary(
            run_id=RUN_ID,
            step=action.step,
            tool_name=action.tool_name,
            ok=True,
            summary=self.summaries[action.tool_name],
        )


def _run(
    provider: _Provider,
    adapter: _Adapter,
    *,
    allowed_tools: frozenset[str],
    context_environ: dict[str, str] | None,
    limits: NativeToolCallingLimits | None = None,
):
    return asyncio.run(
        run_native_tool_calling(
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            goal="ensure stock for ITEM-1",
            provider=provider,
            tool_adapter=adapter,
            allowed_tools=allowed_tools,
            context_environ=context_environ,
            limits=limits,
            skills_enabled=False,
        )
    )


def test_missing_context_budget_stops_before_provider_call() -> None:
    provider = _Provider([ProviderResponse(text="unused")])
    adapter = _Adapter({"item.lookup": "unused"})

    result = _run(
        provider,
        adapter,
        allowed_tools=frozenset({"item.lookup"}),
        context_environ={},
    )

    assert result.stop_reason.code == "CONTEXT_BUDGET"
    assert provider.calls == 0
    assert adapter.calls == []
    assert not any(event.event_type == "model.requested" for event in result.events)
    assert result.events[-1].event_type == "run.stopped"


def test_each_model_request_has_prior_context_metadata_and_no_raw_content() -> None:
    summary = "observed stock is 60"
    digest = observation_from_summary(
        run_id=RUN_ID,
        step=1,
        tool_name="item.lookup",
        ok=True,
        summary=summary,
    ).digest
    provider = _Provider(
        [
            ProviderResponse(
                tool_calls=(ProviderToolCall(id="call-1", name="item.lookup", arguments="{}"),)
            ),
            ProviderResponse(
                text=json.dumps(
                    {
                        "type": "final",
                        "schema_version": "1",
                        "status": "SUCCEEDED",
                        "summary": "observed stock is 60",
                        "evidence_refs": [digest],
                    }
                )
            ),
        ]
    )
    result = _run(
        provider,
        _Adapter({"item.lookup": summary}),
        allowed_tools=frozenset({"item.lookup"}),
        context_environ={CONTEXT_INPUT_TOKEN_BUDGET_ENV: "10000"},
    )

    assert result.stop_reason.code == "FINAL_ANSWER"
    requested = [
        (index, event)
        for index, event in enumerate(result.events)
        if event.event_type == "model.requested"
    ]
    assert requested
    for index, event in requested:
        context_events = [
            candidate
            for candidate in result.events[:index]
            if candidate.event_type == "context.assembled"
            and candidate.payload.get("step") == event.payload.get("step")
        ]
        assert context_events
    for event in result.events:
        if event.event_type in {"context.assembled", "context.compressed"}:
            assert all(
                key not in event.payload
                for key in ("content", "goal", "system_prompt", "skill_body", "context")
            )


def test_actual_provider_prompt_tokens_over_budget_rejects_action() -> None:
    provider = _Provider([ProviderResponse(prompt_tokens=5_001, completion_tokens=1)])
    adapter = _Adapter({"item.lookup": "unused"})

    result = _run(
        provider,
        adapter,
        allowed_tools=frozenset({"item.lookup"}),
        context_environ={CONTEXT_INPUT_TOKEN_BUDGET_ENV: "5000"},
    )

    assert result.stop_reason.code == "CONTEXT_BUDGET"
    assert result.usage.prompt_tokens == 5_001
    assert provider.calls == 1
    assert adapter.calls == []
    actual_events = [
        event
        for event in result.events
        if event.event_type == "context.assembled"
        and event.payload.get("actual_prompt_tokens") == 5_001
    ]
    assert actual_events


def test_compressed_context_is_traced_before_next_provider_call() -> None:
    first = "first observation " + ("x" * 3_700)
    second = "second observation " + ("y" * 3_700)
    provider = _Provider(
        [
            ProviderResponse(
                tool_calls=(ProviderToolCall(id="call-1", name="item.lookup", arguments="{}"),)
            ),
            ProviderResponse(
                tool_calls=(
                    ProviderToolCall(
                        id="call-2",
                        name="stock.projected",
                        arguments='{"item_code":"ITEM-1"}',
                    ),
                )
            ),
            ProviderResponse(
                text=json.dumps(
                    {
                        "type": "final",
                        "schema_version": "1",
                        "status": "SUCCEEDED",
                        "summary": "observations collected",
                        "evidence_refs": [
                            observation_from_summary(
                                run_id=RUN_ID,
                                step=2,
                                tool_name="stock.projected",
                                ok=True,
                                summary=second,
                            ).digest
                        ],
                    }
                )
            ),
        ]
    )
    result = _run(
        provider,
        _Adapter({"item.lookup": first, "stock.projected": second}),
        allowed_tools=frozenset({"item.lookup", "stock.projected"}),
        context_environ={CONTEXT_INPUT_TOKEN_BUDGET_ENV: "8000"},
        limits=NativeToolCallingLimits(max_output_tokens=128, max_total_output_tokens=512),
    )

    compressed = [event for event in result.events if event.event_type == "context.compressed"]
    assert result.stop_reason.code == "FINAL_ANSWER"
    assert compressed
    assert all(
        event.payload["estimated_input_units_after"] <= event.payload["input_budget"]
        for event in compressed
    )
