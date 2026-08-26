"""P4.3 native function-calling tests with deterministic provider responses."""

import asyncio
import json
from collections.abc import Callable
from decimal import Decimal
from time import monotonic
from uuid import UUID

from agent_runtime.agent.budget import Pricing
from agent_runtime.agent.contracts import (
    Action,
    Observation,
    RunResult,
    ToolName,
    observation_from_summary,
)
from agent_runtime.agent.kernel import ToolAdapter
from agent_runtime.agent.native_tool_calling import (
    NativeToolCallingLimits,
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
    *,
    limits: NativeToolCallingLimits | None = None,
    cancelled: Callable[[], bool] | None = None,
    pricing: Pricing | None = None,
    require_pricing: bool = False,
    clock: Callable[[], float] = monotonic,
) -> RunResult:
    return asyncio.run(
        run_native_tool_calling(
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            goal="ensure stock for SYNORA-P1-Item-1001",
            provider=provider,
            tool_adapter=adapter,
            allowed_tools=allowed_tools,
            limits=limits,
            cancelled=cancelled,
            pricing=pricing,
            require_pricing=require_pricing,
            clock=clock,
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


class _CountingProvider:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self.responses = responses
        self.calls = 0
        self.closed = False

    async def complete(self, *_args: object, **_kwargs: object) -> ProviderResponse:
        self.calls += 1
        return self.responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True


class _StepSummaryAdapter:
    def __init__(self, *, same_summary: bool = False) -> None:
        self.same_summary = same_summary
        self.calls: list[Action] = []
        self.closed = False

    async def execute(self, action: Action) -> Observation:
        self.calls.append(action)
        summary = "same" if self.same_summary else f"observation {action.step}"
        return observation_from_summary(
            run_id=RUN_ID,
            step=action.step,
            tool_name=action.tool_name,
            ok=True,
            summary=summary,
        )

    async def aclose(self) -> None:
        self.closed = True


def _tool_response(call_id: str, query: str, *, completion_tokens: int = 0) -> ProviderResponse:
    return ProviderResponse(
        tool_calls=(
            ProviderToolCall(
                id=call_id,
                name="item.lookup",
                arguments=json.dumps({"query": query}),
            ),
        ),
        completion_tokens=completion_tokens,
    )


def test_repeat_guard_stops_native_loop_before_second_identical_call() -> None:
    provider = _CountingProvider(
        [_tool_response("call-1", "bearing"), _tool_response("call-2", "bearing")]
    )
    adapter = _StepSummaryAdapter()

    result = _run(provider, adapter, frozenset({"item.lookup"}))

    assert result.stop_reason.code == "REPEATED_CALL"
    assert len(adapter.calls) == 1
    assert provider.calls == 2


def test_tool_frequency_guard_stops_fourth_distinct_call() -> None:
    provider = _CountingProvider(
        [
            _tool_response(f"call-{index}", query)
            for index, query in enumerate(("a", "b", "c", "d"), 1)
        ]
    )
    adapter = _StepSummaryAdapter()

    result = _run(provider, adapter, frozenset({"item.lookup"}))

    assert result.stop_reason.code == "TOOL_FREQUENCY"
    assert len(adapter.calls) == 3
    assert provider.calls == 4


def test_no_progress_guard_stops_after_two_repeated_observation_digests() -> None:
    provider = _CountingProvider(
        [_tool_response(f"call-{index}", query) for index, query in enumerate(("a", "b", "c"), 1)]
    )
    adapter = _StepSummaryAdapter(same_summary=True)

    result = _run(provider, adapter, frozenset({"item.lookup"}))

    assert result.stop_reason.code == "NO_PROGRESS"
    assert len(adapter.calls) == 3
    assert provider.calls == 3


def test_missing_pricing_fails_closed_before_provider_call() -> None:
    provider = _CountingProvider([ProviderResponse(text='{"status":"SUCCEEDED","summary":"x"}')])
    adapter = _StepSummaryAdapter()

    result = _run(
        provider,
        adapter,
        frozenset({"item.lookup"}),
        require_pricing=True,
    )

    assert result.stop_reason.code == "COST_BUDGET"
    assert provider.calls == 0
    assert adapter.calls == []


def test_per_call_token_budget_fails_closed_after_provider_usage() -> None:
    provider = _CountingProvider([_tool_response("call-1", "bearing", completion_tokens=513)])
    adapter = _StepSummaryAdapter()

    result = _run(provider, adapter, frozenset({"item.lookup"}))

    assert result.stop_reason.code == "TOKEN_BUDGET"
    assert result.usage.completion_tokens == 0
    assert adapter.calls == []


def test_cumulative_token_budget_is_reserved_before_next_provider_call() -> None:
    provider = _CountingProvider(
        [
            _tool_response("call-1", "a", completion_tokens=1),
            _tool_response("call-2", "b", completion_tokens=1),
        ]
    )
    adapter = _StepSummaryAdapter()

    result = _run(
        provider,
        adapter,
        frozenset({"item.lookup"}),
        limits=NativeToolCallingLimits(max_total_output_tokens=1, max_output_tokens=1),
    )

    assert result.stop_reason.code == "TOKEN_BUDGET"
    assert provider.calls == 1
    assert len(adapter.calls) == 1


def test_cost_budget_is_reserved_before_paid_provider_call() -> None:
    provider = _CountingProvider([_tool_response("call-1", "bearing")])
    adapter = _StepSummaryAdapter()
    pricing = Pricing(
        input_microusd_per_million=Decimal("0"),
        output_microusd_per_million=Decimal("1000000"),
        reasoning_microusd_per_million=Decimal("1000000"),
    )

    result = _run(
        provider,
        adapter,
        frozenset({"item.lookup"}),
        pricing=pricing,
        require_pricing=True,
        limits=NativeToolCallingLimits(max_cost_microusd=1),
    )

    assert result.stop_reason.code == "COST_BUDGET"
    assert provider.calls == 0


def test_cancellation_is_checked_before_provider_after_provider_and_after_tool() -> None:
    before = _CountingProvider([_tool_response("call-1", "bearing")])
    before_flag = True
    before_result = _run(
        before,
        _StepSummaryAdapter(),
        frozenset({"item.lookup"}),
        cancelled=lambda: before_flag,
    )
    assert before_result.stop_reason.code == "CANCELLED"
    assert before.calls == 0

    after_provider_flag = False

    class CancellingProvider(_CountingProvider):
        async def complete(self, *_args: object, **_kwargs: object) -> ProviderResponse:
            nonlocal after_provider_flag
            response = await super().complete(*_args, **_kwargs)
            after_provider_flag = True
            return response

    after_provider = CancellingProvider([_tool_response("call-1", "bearing")])
    after_provider_result = _run(
        after_provider,
        _StepSummaryAdapter(),
        frozenset({"item.lookup"}),
        cancelled=lambda: after_provider_flag,
    )
    assert after_provider_result.stop_reason.code == "CANCELLED"
    assert after_provider_result.stop_reason.step == 1

    after_tool_flag = False
    after_tool_adapter = _StepSummaryAdapter()

    async def execute_and_cancel(action: Action) -> Observation:
        nonlocal after_tool_flag
        observation = await _StepSummaryAdapter.execute(after_tool_adapter, action)
        after_tool_flag = True
        return observation

    after_tool_adapter.execute = execute_and_cancel  # type: ignore[method-assign]
    after_tool = _CountingProvider(
        [_tool_response("call-1", "bearing"), ProviderResponse(text="unused")]
    )
    after_tool_result = _run(
        after_tool,
        after_tool_adapter,
        frozenset({"item.lookup"}),
        cancelled=lambda: after_tool_flag,
    )
    assert after_tool_result.stop_reason.code == "CANCELLED"
    assert after_tool.calls == 1


def test_wall_timeout_closes_provider_and_adapter() -> None:
    class SlowProvider(_CountingProvider):
        async def complete(self, *_args: object, **_kwargs: object) -> ProviderResponse:
            await asyncio.sleep(0.05)
            return await super().complete(*_args, **_kwargs)

    provider = SlowProvider([_tool_response("call-1", "bearing")])
    adapter = _StepSummaryAdapter()
    result = _run(
        provider,
        adapter,
        frozenset({"item.lookup"}),
        limits=NativeToolCallingLimits(max_wall_time_ms=5),
    )

    assert result.stop_reason.code == "WALL_TIME_BUDGET"
    assert provider.closed is True
    assert adapter.closed is True
