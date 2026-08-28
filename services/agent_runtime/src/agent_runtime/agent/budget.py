"""Fail-closed token, cost, and wall-clock accounting for Agent runs."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from time import monotonic

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.agent.contracts import BudgetSnapshot, StopCode, UsageSnapshot, canonical_json
from agent_runtime.providers import ProviderMessage, ProviderResponse, ProviderToolSpec

MICROUSD_PER_MILLION = Decimal(1_000_000)
PRICE_INPUT_ENV = "SYNORA_PRICE_INPUT_MICROUSD_PER_MILLION"
PRICE_OUTPUT_ENV = "SYNORA_PRICE_OUTPUT_MICROUSD_PER_MILLION"
PRICE_REASONING_ENV = "SYNORA_PRICE_REASONING_MICROUSD_PER_MILLION"


class BudgetLimits(BaseModel):
    """Balanced P4.4 defaults shared by native execution and tests."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    max_steps: int = Field(default=6, ge=1, le=64)
    max_output_tokens: int = Field(default=512, ge=1, le=512)
    max_total_output_tokens: int = Field(default=3_072, ge=1, le=3_072)
    max_wall_time_ms: int = Field(default=180_000, ge=1, le=180_000)
    max_cost_microusd: int = Field(default=50_000, ge=0, le=50_000)
    max_calls_per_tool: int = Field(default=3, ge=1, le=3)
    no_progress_threshold: int = Field(default=2, ge=1, le=2)


class PricingConfigurationError(ValueError):
    """Environment pricing is incomplete or not a finite non-negative number."""


class Pricing(BaseModel):
    """Prices expressed as micro-USD per one million tokens."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    input_microusd_per_million: Decimal = Field(ge=0)
    output_microusd_per_million: Decimal = Field(ge=0)
    reasoning_microusd_per_million: Decimal = Field(ge=0)

    def cost_microusd(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        reasoning_tokens: int,
    ) -> int:
        values = (
            (prompt_tokens, self.input_microusd_per_million),
            (completion_tokens, self.output_microusd_per_million),
            (reasoning_tokens, self.reasoning_microusd_per_million),
        )
        total = sum(
            (Decimal(tokens) * price / MICROUSD_PER_MILLION for tokens, price in values),
            Decimal(0),
        )
        return int(total.to_integral_value(rounding=ROUND_CEILING))

    def maximum_output_cost_microusd(self, output_tokens: int) -> int:
        """Conservatively price a max-output reservation at the higher output rate."""
        highest_price = (
            self.output_microusd_per_million
            if self.output_microusd_per_million >= self.reasoning_microusd_per_million
            else self.reasoning_microusd_per_million
        )
        total = Decimal(output_tokens) * highest_price / MICROUSD_PER_MILLION
        return int(total.to_integral_value(rounding=ROUND_CEILING))


def pricing_from_environment(
    environ: Mapping[str, str] | None = None,
) -> Pricing | None:
    """Read all three non-secret prices; missing all means local/free mode."""
    values = os.environ if environ is None else environ
    names = (PRICE_INPUT_ENV, PRICE_OUTPUT_ENV, PRICE_REASONING_ENV)
    present = [values.get(name) for name in names]
    if all(value is None or value == "" for value in present):
        return None
    if any(value is None or value == "" for value in present):
        raise PricingConfigurationError("all three pricing environment values are required")
    parsed: list[Decimal] = []
    for value in present:
        assert value is not None
        try:
            price = Decimal(value)
        except (InvalidOperation, ValueError) as error:
            raise PricingConfigurationError("pricing values must be decimal numbers") from error
        if not price.is_finite() or price < 0:
            raise PricingConfigurationError("pricing values must be finite and non-negative")
        parsed.append(price)
    return Pricing(
        input_microusd_per_million=parsed[0],
        output_microusd_per_million=parsed[1],
        reasoning_microusd_per_million=parsed[2],
    )


def estimate_input_units(
    messages: Sequence[ProviderMessage],
    tools: Sequence[ProviderToolSpec],
) -> int:
    """Use UTF-8 bytes as a conservative estimate before a provider call.

    This is intentionally named ``units``: it is not the provider's real
    tokenizer and must never be presented as actual prompt-token usage.
    """
    message_bytes = sum(
        len(canonical_json(message.model_dump(mode="json")).encode("utf-8")) for message in messages
    )
    tool_bytes = sum(
        len(canonical_json(tool.model_dump(mode="json")).encode("utf-8")) for tool in tools
    )
    return message_bytes + tool_bytes


def estimate_input_tokens(
    messages: Sequence[ProviderMessage],
    tools: Sequence[ProviderToolSpec],
) -> int:
    """Backward-compatible alias for the preflight estimate."""
    return estimate_input_units(messages, tools)


@dataclass
class BudgetAccount:
    """Mutable per-run account; no secret-bearing request text is retained."""

    limits: BudgetLimits
    pricing: Pricing | None
    require_pricing: bool
    started: float
    clock: Callable[[], float] = monotonic
    usage: UsageSnapshot = field(default_factory=UsageSnapshot)

    def elapsed_ms(self) -> int:
        return max(0, int((self.clock() - self.started) * 1000))

    def snapshot(self, *, steps: int) -> BudgetSnapshot:
        return BudgetSnapshot(
            steps=steps,
            prompt_tokens=self.usage.prompt_tokens,
            completion_tokens=self.usage.completion_tokens,
            reasoning_tokens=self.usage.reasoning_tokens,
            cost_microusd=self.usage.cost_microusd,
            elapsed_ms=self.elapsed_ms(),
        )

    def preflight(
        self,
        *,
        messages: Sequence[ProviderMessage],
        tools: Sequence[ProviderToolSpec],
    ) -> StopCode | None:
        """Return a StopCode before a paid provider call, otherwise ``None``."""
        if self.elapsed_ms() >= self.limits.max_wall_time_ms:
            return "WALL_TIME_BUDGET"
        if (
            self.usage.completion_tokens
            + self.usage.reasoning_tokens
            + self.limits.max_output_tokens
            > self.limits.max_total_output_tokens
        ):
            return "TOKEN_BUDGET"
        if self.require_pricing and self.pricing is None:
            return "COST_BUDGET"
        if self.pricing is None:
            return None
        estimated_input_units = estimate_input_units(messages, tools)
        reservation = self.pricing.cost_microusd(
            prompt_tokens=estimated_input_units,
            completion_tokens=0,
            reasoning_tokens=0,
        ) + self.pricing.maximum_output_cost_microusd(self.limits.max_output_tokens)
        if self.usage.cost_microusd + reservation > self.limits.max_cost_microusd:
            return "COST_BUDGET"
        return None

    def record(self, response: ProviderResponse) -> StopCode | None:
        """Account actual provider usage and return an exceeded budget code."""
        per_call_output = response.completion_tokens + response.reasoning_tokens
        if (
            min(
                response.prompt_tokens,
                response.completion_tokens,
                response.reasoning_tokens,
            )
            < 0
        ):
            return "TOKEN_BUDGET"
        cumulative_output = (
            self.usage.completion_tokens + self.usage.reasoning_tokens + per_call_output
        )
        next_cost = self.usage.cost_microusd
        if self.pricing is not None:
            next_cost += self.pricing.cost_microusd(
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                reasoning_tokens=response.reasoning_tokens,
            )
        self.usage = UsageSnapshot(
            prompt_tokens=self.usage.prompt_tokens + response.prompt_tokens,
            completion_tokens=self.usage.completion_tokens + response.completion_tokens,
            reasoning_tokens=self.usage.reasoning_tokens + response.reasoning_tokens,
            cost_microusd=next_cost,
        )
        # Keep provider-reported usage as audit evidence even when that usage
        # itself exceeds the per-call cap; the caller still stops fail-closed.
        if per_call_output > self.limits.max_output_tokens:
            return "TOKEN_BUDGET"
        if cumulative_output > self.limits.max_total_output_tokens:
            return "TOKEN_BUDGET"
        if next_cost > self.limits.max_cost_microusd:
            return "COST_BUDGET"
        if self.elapsed_ms() >= self.limits.max_wall_time_ms:
            return "WALL_TIME_BUDGET"
        return None
