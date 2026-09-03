"""P9.6 local MCP surface for the deterministic procurement-risk function.

This module is deliberately outside the runtime service.  It exposes one
read-only tool over the official MCP SDK and never imports Frappe, a provider,
or any application gateway capability.
"""

from __future__ import annotations

import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final, Literal

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, field_validator

from synora_agentic_erp.agent.analysis import (
    DemandLine,
    IncomingLine,
    ItemInput,
    analyze_item,
)

TOOL_NAME: Final = "synora.procurement_risk.analyze"
SERVER_NAME: Final = "synora-phase9-procurement-risk"
SCHEMA_VERSION: Final = "1"

_ITEM_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SENSITIVE_ENV = re.compile(r"(?:API[_-]?KEY|TOKEN|COOKIE|SECRET|PROXY)", re.IGNORECASE)


class _StrictWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _validate_decimal_text(value: str, *, field_name: str) -> str:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field_name} must be a decimal string") from error
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if parsed.adjusted() > 24 or parsed.adjusted() < -24:
        raise ValueError(f"{field_name} is outside the supported magnitude")
    return value


class ProcurementRiskLine(_StrictWireModel):
    qty: str = Field(min_length=1, max_length=80)
    schedule_date: str = Field(pattern=_ISO_DATE.pattern)

    @field_validator("qty")
    @classmethod
    def validate_qty(cls, value: str) -> str:
        return _validate_decimal_text(value, field_name="qty")

    @field_validator("schedule_date")
    @classmethod
    def validate_schedule_date(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise ValueError("schedule_date must be a real ISO date") from error
        return value


class ProcurementRiskRequest(_StrictWireModel):
    item_code: str = Field(pattern=_ITEM_CODE.pattern)
    actual_qty: str | None = Field(default=None, max_length=80)
    horizon: str = Field(pattern=_ISO_DATE.pattern)
    demand_lines: list[ProcurementRiskLine] = Field(default_factory=list, max_length=128)
    incoming_lines: list[ProcurementRiskLine] = Field(default_factory=list, max_length=128)
    open_mr_qty: str | None = Field(default=None, max_length=80)

    @field_validator("actual_qty", "open_mr_qty")
    @classmethod
    def validate_optional_qty(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "quantity")
        return _validate_decimal_text(value, field_name=field_name)

    @field_validator("horizon")
    @classmethod
    def validate_horizon(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise ValueError("horizon must be a real ISO date") from error
        return value


RiskCode = Literal[
    "SHORTAGE",
    "ADEQUATE",
    "DUPLICATE_RISK",
    "NO_DEMAND",
    "NEEDS_INPUT",
    "UNKNOWN",
]


class ProcurementRiskResponse(_StrictWireModel):
    schema_version: Literal["1"] = SCHEMA_VERSION
    item_code: str
    risk: RiskCode
    actual_qty: str
    demand_qty: str
    incoming_qty: str
    open_mr_qty: str
    net_position: str
    shortage_qty: str
    unknowns: list[str]


def _decimal(value: str, *, field_name: str) -> Decimal:
    _validate_decimal_text(value, field_name=field_name)
    return Decimal(value)


def _line_values(lines: list[ProcurementRiskLine]) -> tuple[DemandLine, ...]:
    return tuple(
        DemandLine(
            qty=_decimal(line.qty, field_name="line.qty"),
            schedule_date=date.fromisoformat(line.schedule_date),
        )
        for line in lines
    )


def _incoming_values(lines: list[ProcurementRiskLine]) -> tuple[IncomingLine, ...]:
    return tuple(
        IncomingLine(
            qty=_decimal(line.qty, field_name="line.qty"),
            schedule_date=date.fromisoformat(line.schedule_date),
        )
        for line in lines
    )


def analyze_procurement_risk(request: ProcurementRiskRequest) -> ProcurementRiskResponse:
    """Analyze one item using the existing deterministic, read-only function."""

    result = analyze_item(
        ItemInput(
            item_code=request.item_code,
            actual_qty=(
                None
                if request.actual_qty is None
                else _decimal(request.actual_qty, field_name="actual_qty")
            ),
            horizon=date.fromisoformat(request.horizon),
            demand_lines=_line_values(request.demand_lines),
            incoming_lines=_incoming_values(request.incoming_lines),
            open_mr_qty=(
                None
                if request.open_mr_qty is None
                else _decimal(request.open_mr_qty, field_name="open_mr_qty")
            ),
        )
    )
    return ProcurementRiskResponse.model_validate(
        {"schema_version": SCHEMA_VERSION, **result.to_dict()}
    )


server = MCPServer(
    name=SERVER_NAME,
    version=SCHEMA_VERSION,
    description="LAB_ONLY read-only deterministic procurement risk analysis",
    instructions="Only analyze the supplied typed item snapshot. Do not write or fetch data.",
)
server.add_tool(
    analyze_procurement_risk,
    name=TOOL_NAME,
    description="Analyze one supplied procurement item snapshot without ERP or network access.",
    annotations=ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    structured_output=True,
)


def scrub_sensitive_environment() -> tuple[str, ...]:
    """Remove credential/proxy-like variables before the stdio server starts."""

    removed: list[str] = []
    for key in tuple(os.environ):
        if _SENSITIVE_ENV.search(key):
            removed.append(key)
            os.environ.pop(key, None)
    return tuple(sorted(removed))


def run_stdio() -> None:
    """Run the MCP server with stdout reserved for the protocol stream."""

    scrub_sensitive_environment()
    server.run("stdio")
