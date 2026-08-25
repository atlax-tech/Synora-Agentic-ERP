"""P3.3 确定性采购分析纯函数测试 (PRD F-003)。

固定输入必须得到固定结果; 缺失输入返回 NEEDS_INPUT; 冲突数据返回
UNKNOWN; LLM 不参与任何计算。
"""

from datetime import date
from decimal import Decimal

from synora_agentic_erp.agent.analysis import (
    ADEQUATE,
    DUPLICATE_RISK,
    NEEDS_INPUT,
    NO_DEMAND,
    SHORTAGE,
    UNKNOWN,
    DemandLine,
    IncomingLine,
    ItemInput,
    analyze_item,
    horizon_date,
)

TODAY = date(2026, 8, 25)


def _input(
    *,
    actual: str | None,
    horizon: date | None = None,
    demand: tuple[tuple[str, str], ...] = (),
    incoming: tuple[tuple[str, str], ...] = (),
    open_mr: str | None = "0",
) -> ItemInput:
    return ItemInput(
        item_code="ITEM-1",
        actual_qty=None if actual is None else Decimal(actual),
        horizon=horizon if horizon is not None else horizon_date(TODAY, 90),
        demand_lines=tuple(DemandLine(Decimal(q), date.fromisoformat(d)) for q, d in demand),
        incoming_lines=tuple(IncomingLine(Decimal(q), date.fromisoformat(d)) for q, d in incoming),
        open_mr_qty=None if open_mr is None else Decimal(open_mr),
    )


def test_same_input_produces_same_result() -> None:
    inp = _input(actual="100", demand=(("50", "2026-09-01"),), open_mr="50")
    first = analyze_item(inp)
    second = analyze_item(inp)
    assert first == second
    assert first.to_dict() == second.to_dict()


def test_shortage_when_net_position_negative() -> None:
    # 库存 100 + 在途 20 - 需求 150 = -30 -> 缺货 30
    result = analyze_item(
        _input(actual="100", demand=(("150", "2026-09-10"),), incoming=(("20", "2026-09-05"),))
    )
    assert result.risk == SHORTAGE
    assert result.net_position == Decimal("-30")
    assert result.shortage_qty == Decimal("30")


def test_window_filters_out_future_demand() -> None:
    # 需求在 90 天窗口之外 -> 不计入缺货
    result = analyze_item(_input(actual="100", demand=(("200", "2027-06-01"),), open_mr="200"))
    assert result.risk == NO_DEMAND
    assert result.demand_qty == Decimal("0")


def test_duplicate_risk_when_open_mr_covers_demand() -> None:
    # 覆盖充足 (net >= 0) 但仍有未结 MR -> 重复采购风险
    result = analyze_item(_input(actual="100", demand=(("50", "2026-09-01"),), open_mr="50"))
    assert result.risk == DUPLICATE_RISK
    assert result.net_position >= Decimal("0")


def test_duplicate_risk_when_incoming_covers_demand() -> None:
    # 在途 PO 覆盖需求且 net >= 0 -> 重复采购风险
    result = analyze_item(
        _input(actual="10", demand=(("50", "2026-09-01"),), incoming=(("50", "2026-08-30"),))
    )
    assert result.risk == DUPLICATE_RISK


def test_adequate_when_covered_without_open_supply() -> None:
    result = analyze_item(_input(actual="100", demand=(("50", "2026-09-01"),), open_mr="0"))
    assert result.risk == ADEQUATE
    assert result.net_position == Decimal("50")


def test_no_demand_risk() -> None:
    result = analyze_item(_input(actual="100", open_mr="0"))
    assert result.risk == NO_DEMAND


def test_needs_input_when_required_missing() -> None:
    result = analyze_item(_input(actual="100", open_mr=None))
    assert result.risk == NEEDS_INPUT
    assert result.unknowns == ("open_mr_qty",)
    result = analyze_item(_input(actual=None, open_mr="0"))
    assert result.risk == NEEDS_INPUT
    assert "actual_qty" in result.unknowns


def test_unknown_on_conflicting_negative_quantities() -> None:
    result = analyze_item(_input(actual="-5", open_mr="0"))
    assert result.risk == UNKNOWN
    assert "negative_actual_qty" in result.unknowns
    result = analyze_item(_input(actual="10", demand=(("-3", "2026-09-01"),), open_mr="0"))
    assert result.risk == UNKNOWN


def test_decimal_precision_is_exact() -> None:
    result = analyze_item(
        _input(actual="0.1", demand=(("0.3", "2026-09-01"),), incoming=(("0.2", "2026-09-02"),))
    )
    assert result.net_position == Decimal("0.0")
    assert result.shortage_qty == Decimal("0.0")


def test_horizon_date_adds_window() -> None:
    assert horizon_date(TODAY, 90) == date(2026, 11, 23)
    assert horizon_date(TODAY, 0) == TODAY


def test_unknowns_are_listed_not_estimated() -> None:
    result = analyze_item(_input(actual="100", open_mr="0"))
    assert result.unknowns == ()
