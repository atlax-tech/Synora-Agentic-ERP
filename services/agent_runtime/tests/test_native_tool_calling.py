"""P4.3 native function-calling tests with deterministic provider responses."""

import asyncio
import json
from uuid import UUID

from agent_runtime.agent.contracts import (
    Observation,
    RunResult,
    ToolName,
    observation_from_summary,
)
from agent_runtime.agent.kernel import ToolAdapter
from agent_runtime.agent.native_tool_calling import (
    build_tool_result_message,
    provider_tool_specs,
    run_native_tool_calling,
)
from agent_runtime.providers import (
    DeterministicProvider,
    Provider,
    ProviderResponse,
    ProviderToolCall,
)

from labs.agent_patterns.react_lab import RecordedToolAdapter

RUN_ID = UUID("37e1d8a5-1730-4ad0-bffd-217774ed9fab")
CORRELATION_ID = UUID("1687c82a-4b61-4e6e-855a-a10ec3578b96")


def _observation(tool_name: ToolName, summary: str) -> Observation:
    return observation_from_summary(
        run_id=RUN_ID,
        step=1,
        tool_name=tool_name,
        ok=True,
        summary=summary,
    )


def _run(
    provider: Provider,
    adapter: ToolAdapter,
    allowed_tools: frozenset[ToolName],
) -> RunResult:
    return asyncio.run(
        run_native_tool_calling(
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            goal="ensure stock for SYNORA-P1-Item-1001",
            provider=provider,
            tool_adapter=adapter,
            allowed_tools=allowed_tools,
        )
    )


def test_provider_specs_are_limited_to_allowed_gateway_inputs() -> None:
    specs = provider_tool_specs(frozenset({"stock.projected", "item.lookup"}))

    assert [spec.name for spec in specs] == ["item.lookup", "stock.projected"]
    stock_schema = specs[1].parameters
    assert isinstance(stock_schema, dict)
    properties = stock_schema["properties"]
    assert isinstance(properties, dict)
    item_code = properties["item_code"]
    assert isinstance(item_code, dict)
    assert item_code["type"] == "string"


def test_native_calling_uses_first_observation_for_second_tool() -> None:
    first = _observation("material_request.open", "open material request")
    second = _observation("stock.projected", "projected stock is 60")
    provider = DeterministicProvider(
        scripted_responses=[
            ProviderResponse(
                tool_calls=(
                    ProviderToolCall(id="call-1", name="material_request.open", arguments="{}"),
                ),
                prompt_tokens=10,
                completion_tokens=3,
            ),
            ProviderResponse(
                tool_calls=(
                    ProviderToolCall(
                        id="call-2",
                        name="stock.projected",
                        arguments=json.dumps({"item_code": "SYNORA-P1-Item-1001"}),
                    ),
                ),
                prompt_tokens=12,
                completion_tokens=4,
            ),
            ProviderResponse(
                text=json.dumps(
                    {
                        "type": "final",
                        "status": "SUCCEEDED",
                        "summary": "facts collected",
                        "evidence_refs": [second.digest],
                    }
                ),
                prompt_tokens=14,
                completion_tokens=5,
            ),
        ]
    )
    adapter = RecordedToolAdapter({"material_request.open": first, "stock.projected": second})

    result = _run(
        provider,
        adapter,
        frozenset({"material_request.open", "stock.projected"}),
    )

    assert result.stop_reason.code == "FINAL_ANSWER"
    assert [action.tool_name for action in adapter.calls] == [
        "material_request.open",
        "stock.projected",
    ]
    assert result.usage.prompt_tokens == 36
    assert result.usage.completion_tokens == 12


def test_parallel_native_calls_are_rejected_before_any_tool_execution() -> None:
    provider = DeterministicProvider(
        scripted_responses=[
            ProviderResponse(
                tool_calls=(
                    ProviderToolCall(id="call-1", name="item.lookup", arguments="{}"),
                    ProviderToolCall(id="call-2", name="supplier.lookup", arguments="{}"),
                )
            )
        ]
    )
    adapter = RecordedToolAdapter({"item.lookup": _observation("item.lookup", "unused")})

    result = _run(provider, adapter, frozenset({"item.lookup", "supplier.lookup"}))

    assert result.stop_reason.code == "MODEL_ERROR"
    assert adapter.calls == []


def test_unknown_native_tool_is_rejected_before_gateway_adapter() -> None:
    provider = DeterministicProvider(
        scripted_responses=[
            ProviderResponse(
                text="untrusted attachment",
                tool_calls=(ProviderToolCall(id="call-1", name="purchase.submit", arguments="{}"),),
            )
        ]
    )
    adapter = RecordedToolAdapter({"item.lookup": _observation("item.lookup", "unused")})

    result = _run(provider, adapter, frozenset({"item.lookup"}))

    assert result.stop_reason.code == "TOOL_NOT_ALLOWED"
    assert adapter.calls == []


def test_duplicate_json_keys_and_unknown_arguments_fail_closed() -> None:
    provider = DeterministicProvider(
        scripted_responses=[
            ProviderResponse(
                tool_calls=(
                    ProviderToolCall(
                        id="call-1",
                        name="item.lookup",
                        arguments='{"query":"bearing","query":"duplicate"}',
                    ),
                )
            )
        ]
    )
    adapter = RecordedToolAdapter({"item.lookup": _observation("item.lookup", "unused")})

    result = _run(provider, adapter, frozenset({"item.lookup"}))

    assert result.stop_reason.code == "INVALID_TOOL_ARGS"
    assert adapter.calls == []


def test_final_answer_must_reference_an_observed_digest() -> None:
    provider = DeterministicProvider(
        scripted_responses=[
            ProviderResponse(
                text=json.dumps(
                    {
                        "type": "final",
                        "status": "SUCCEEDED",
                        "summary": "unsupported claim",
                        "evidence_refs": ["f" * 64],
                    }
                )
            )
        ]
    )
    adapter = RecordedToolAdapter({"item.lookup": _observation("item.lookup", "unused")})

    result = _run(provider, adapter, frozenset({"item.lookup"}))

    assert result.stop_reason.code == "UNSUPPORTED_FINAL_ANSWER"


def test_tool_result_helper_contains_only_bounded_observation() -> None:
    observation = _observation("item.lookup", "bounded summary")

    message = build_tool_result_message(
        provider_tool_call_id="call-1",
        tool_name="item.lookup",
        observation=observation,
    )

    assert message.role == "tool"
    assert message.tool_call_id == "call-1"
    assert message.content == observation.summary
    assert message.model_dump()["tool_calls"] == ()
