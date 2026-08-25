"""P3.5 可解释只读计划纯函数测试 (确定性骨架, 无 frappe 依赖)。"""

from synora_agentic_erp.agent.plan import (
    AnalysisRow,
    build_plan,
    match_goal_items,
)


def _row(item: str, risk: str, net: str = "0", shortage: str = "0") -> AnalysisRow:
    return AnalysisRow(
        item_code=item,
        risk=risk,
        actual_qty="100",
        demand_qty="50",
        incoming_qty="20",
        open_mr_qty="5",
        net_position=net,
        shortage_qty=shortage,
        unknowns="",
    )


def test_match_goal_items_is_case_insensitive_and_whole_word() -> None:
    items = ("SYNORA-P1-Item-1001", "BEARING-200")
    assert match_goal_items("ensure stock for SYNORA-P1-Item-1001", items) == (
        "SYNORA-P1-Item-1001",
    )
    assert match_goal_items("synora-p1-item-1001 urgent", items) == ("SYNORA-P1-Item-1001",)
    # 前缀相同不算整词匹配
    assert match_goal_items("need ITEM-10010", items) == ()


def test_build_plan_produces_deterministic_structure() -> None:
    analyses = (
        _row("SYNORA-P1-Item-1001", "DUPLICATE_RISK", net="60"),
        _row("SYNORA-P1-Item-1002", "SHORTAGE", net="-30", shortage="30"),
    )
    plan = build_plan(
        goal="ensure SYNORA-P1-Item-1001 stock",
        horizon_days=90,
        company="SYNORA-P1 Test Company",
        warehouse=None,
        analyses=analyses,
        generated_at="2026-08-25T20:00:00+08:00",
    )
    assert plan.goal == "ensure SYNORA-P1-Item-1001 stock"
    assert plan.horizon_days == 90
    assert len(plan.findings) == 2
    # 缺货排在最前
    assert plan.findings[0].item_code == "SYNORA-P1-Item-1002"
    assert plan.findings[0].recommendation.startswith("建议补货")
    assert plan.findings[1].recommendation.startswith("不建议重复采购")
    # 目标匹配标记
    by_item = {f.item_code: f for f in plan.findings}
    assert by_item["SYNORA-P1-Item-1001"].matched_goal is True
    assert by_item["SYNORA-P1-Item-1002"].matched_goal is False
    # 每项都带证据
    assert all(f.evidence for f in plan.findings)
    assert "共分析 2 个物料：1 个缺货、1 个重复采购风险" in plan.summary


def test_same_input_same_plan() -> None:
    analyses = (_row("A", "SHORTAGE", net="-5", shortage="5"),)
    first = build_plan(
        goal="stock A",
        horizon_days=90,
        company="C",
        warehouse=None,
        analyses=analyses,
        generated_at="2026-08-25T20:00:00+08:00",
    )
    second = build_plan(
        goal="stock A",
        horizon_days=90,
        company="C",
        warehouse=None,
        analyses=analyses,
        generated_at="2026-08-25T20:00:00+08:00",
    )
    assert first.to_dict() == second.to_dict()


def test_needs_input_finding_explains_missing_data() -> None:
    analyses = (
        AnalysisRow(
            item_code="X-1",
            risk="NEEDS_INPUT",
            actual_qty="0",
            demand_qty="0",
            incoming_qty="0",
            open_mr_qty="0",
            net_position="0",
            shortage_qty="0",
            unknowns="actual_qty,open_mr_qty",
        ),
    )
    plan = build_plan(
        goal="check X-1",
        horizon_days=90,
        company="C",
        warehouse="WH",
        analyses=analyses,
        generated_at="2026-08-25T20:00:00+08:00",
    )
    finding = plan.findings[0]
    assert finding.risk == "NEEDS_INPUT"
    assert "输入不足" in finding.recommendation
    assert "actual_qty" in finding.recommendation
    assert "不估算" in finding.recommendation or "无法判定" in finding.recommendation
