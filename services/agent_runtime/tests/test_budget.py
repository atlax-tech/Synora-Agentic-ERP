"""P4.4 fail-closed accounting tests for tokens, cost, and wall time."""

from decimal import Decimal

import pytest
from agent_runtime.agent.budget import (
    PRICE_INPUT_ENV,
    PRICE_OUTPUT_ENV,
    PRICE_REASONING_ENV,
    BudgetAccount,
    BudgetLimits,
    Pricing,
    PricingConfigurationError,
    estimate_input_tokens,
    pricing_from_environment,
)
from agent_runtime.agent.contracts import UsageSnapshot
from agent_runtime.providers import ProviderMessage, ProviderResponse, ProviderToolSpec


def _pricing(
    *,
    input_rate: str = "1000",
    output_rate: str = "2000",
    reasoning_rate: str = "3000",
) -> Pricing:
    return Pricing(
        input_microusd_per_million=Decimal(input_rate),
        output_microusd_per_million=Decimal(output_rate),
        reasoning_microusd_per_million=Decimal(reasoning_rate),
    )


def _messages_and_tools() -> tuple[list[ProviderMessage], list[ProviderToolSpec]]:
    return (
        [ProviderMessage(role="user", content="库存")],
        [ProviderToolSpec(name="item.lookup", description="read", parameters={"type": "object"})],
    )


def test_pricing_uses_decimal_and_rounds_up_micro_usd() -> None:
    pricing = _pricing()

    assert (
        pricing.cost_microusd(
            prompt_tokens=1_001,
            completion_tokens=2_001,
            reasoning_tokens=3_001,
        )
        == 15
    )
    assert pricing.maximum_output_cost_microusd(512) == 2


def test_pricing_environment_requires_all_three_finite_non_negative_values() -> None:
    empty: dict[str, str] = {}
    assert pricing_from_environment(empty) is None

    partial = {PRICE_INPUT_ENV: "1"}
    with pytest.raises(PricingConfigurationError):
        pricing_from_environment(partial)

    invalid = {
        PRICE_INPUT_ENV: "NaN",
        PRICE_OUTPUT_ENV: "2",
        PRICE_REASONING_ENV: "3",
    }
    with pytest.raises(PricingConfigurationError):
        pricing_from_environment(invalid)

    negative = {
        PRICE_INPUT_ENV: "1",
        PRICE_OUTPUT_ENV: "-2",
        PRICE_REASONING_ENV: "3",
    }
    with pytest.raises(PricingConfigurationError):
        pricing_from_environment(negative)


def test_input_token_estimate_is_based_on_utf8_bytes() -> None:
    messages, tools = _messages_and_tools()

    estimate = estimate_input_tokens(messages, tools)

    assert estimate == sum(
        len(message.model_dump_json().encode("utf-8")) for message in messages
    ) + sum(len(tool.model_dump_json().encode("utf-8")) for tool in tools)
    assert estimate > len("库存".encode())


def test_preflight_requires_pricing_before_provider_call() -> None:
    messages, tools = _messages_and_tools()
    account = BudgetAccount(
        limits=BudgetLimits(),
        pricing=None,
        require_pricing=True,
        started=0.0,
        clock=lambda: 1.0,
    )

    assert account.preflight(messages=messages, tools=tools) == "COST_BUDGET"


def test_preflight_reserves_output_and_actual_record_enforces_cumulative_limits() -> None:
    messages, tools = _messages_and_tools()
    limits = BudgetLimits(max_total_output_tokens=10, max_output_tokens=6)
    account = BudgetAccount(
        limits=limits,
        pricing=_pricing(input_rate="0", output_rate="1000000", reasoning_rate="1000000"),
        require_pricing=True,
        started=0.0,
        clock=lambda: 1.0,
    )

    assert account.preflight(messages=messages, tools=tools) is None
    assert account.record(ProviderResponse(completion_tokens=4)) is None
    assert account.usage == UsageSnapshot(completion_tokens=4, cost_microusd=4)
    assert account.record(ProviderResponse(completion_tokens=6)) is None
    assert account.record(ProviderResponse(completion_tokens=1)) == "TOKEN_BUDGET"
    assert account.usage.completion_tokens == 11


def test_actual_cost_budget_is_checked_after_provider_usage() -> None:
    account = BudgetAccount(
        limits=BudgetLimits(max_cost_microusd=1),
        pricing=_pricing(input_rate="1000000", output_rate="0", reasoning_rate="0"),
        require_pricing=True,
        started=0.0,
        clock=lambda: 1.0,
    )

    assert account.record(ProviderResponse(prompt_tokens=2)) == "COST_BUDGET"
    assert account.usage.cost_microusd == 2


def test_wall_budget_is_checked_in_preflight() -> None:
    messages, tools = _messages_and_tools()
    account = BudgetAccount(
        limits=BudgetLimits(max_wall_time_ms=1),
        pricing=_pricing(),
        require_pricing=True,
        started=0.0,
        clock=lambda: 0.002,
    )

    assert account.preflight(messages=messages, tools=tools) == "WALL_TIME_BUDGET"
