"""P3.5 单 Agent 可解释只读计划 (PRD F-003/F-004 前身, 只读无写入)。

确定性骨架: 基于 P3.3 分析结果生成可解释计划——目标理解 (goal 中的
item 线索)、风险摘要、逐项建议、来源引用与未知项; LLM 不参与数量/
金额/阈值计算。真实模型的自然语言增强在 BYOK 配置生效后接入, 但
数量与阈值仍由本模块的确定性规则决定。
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

SHORTAGE = "SHORTAGE"
ADEQUATE = "ADEQUATE"
DUPLICATE_RISK = "DUPLICATE_RISK"
NO_DEMAND = "NO_DEMAND"
NEEDS_INPUT = "NEEDS_INPUT"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class AnalysisRow:
    """来自 Synora Item Analysis 的确定性计算结果。"""

    item_code: str
    risk: str
    actual_qty: str
    demand_qty: str
    incoming_qty: str
    open_mr_qty: str
    net_position: str
    shortage_qty: str
    unknowns: str


@dataclass(frozen=True)
class PlanFinding:
    item_code: str
    risk: str
    recommendation: str
    evidence: tuple[str, ...]
    matched_goal: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "item_code": self.item_code,
            "risk": self.risk,
            "recommendation": self.recommendation,
            "evidence": list(self.evidence),
            "matched_goal": self.matched_goal,
        }


@dataclass(frozen=True)
class PlanResult:
    goal: str
    horizon_days: int
    company: str
    warehouse: str | None
    summary: str
    findings: tuple[PlanFinding, ...]
    generated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "goal": self.goal,
            "horizon_days": self.horizon_days,
            "company": self.company,
            "warehouse": self.warehouse,
            "summary": self.summary,
            "findings": [finding.to_dict() for finding in self.findings],
            "generated_at": self.generated_at,
        }


def match_goal_items(goal: str, known_items: tuple[str, ...]) -> tuple[str, ...]:
    """目标理解: 返回 goal 中出现的已知 item_code (整词、大小写不敏感)。"""
    normalized = goal.lower()
    return tuple(
        item
        for item in known_items
        if re.search(rf"(?<![a-z0-9_-]){re.escape(item.lower())}(?![a-z0-9_-])", normalized)
    )


def _recommendation(row: AnalysisRow, matched_goal: bool) -> str:
    item = row.item_code
    if row.risk == SHORTAGE:
        return (
            f"建议补货 {item}：库存 {row.actual_qty} + 在途 {row.incoming_qty} "
            f"- 需求 {row.demand_qty} = {row.net_position} < 0，缺口 {row.shortage_qty}。"
        )
    if row.risk == DUPLICATE_RISK:
        return (
            f"不建议重复采购 {item}：库存 {row.actual_qty} + 在途 {row.incoming_qty} "
            f"- 需求 {row.demand_qty} = {row.net_position} ≥ 0，且已有未结需求计划/在途供应。"
        )
    if row.risk == ADEQUATE:
        return f"供应充足 {item}：净位置 {row.net_position} ≥ 0 且无未结采购计划。"
    if row.risk == NO_DEMAND:
        return f"窗口内无需求 {item}：无未结需求与在途，无需关注。"
    if row.risk == NEEDS_INPUT:
        return f"输入不足 {item}：缺少 {row.unknowns or '必需数据'}，无法判定风险（不做估算）。"
    return f"数据异常 {item}：{row.unknowns or '数据冲突'}，需人工核对。"


def _evidence(row: AnalysisRow) -> tuple[str, ...]:
    return (
        f"risk={row.risk}",
        f"net = actual {row.actual_qty} + incoming {row.incoming_qty} - demand {row.demand_qty}",
        f"open_mr={row.open_mr_qty}, shortage={row.shortage_qty}",
    )


def build_plan(
    *,
    goal: str,
    horizon_days: int,
    company: str,
    warehouse: str | None,
    analyses: tuple[AnalysisRow, ...],
    generated_at: str | None = None,
) -> PlanResult:
    """由确定性分析结果生成可解释只读计划 (同一输入恒得同一输出结构)。"""
    known_items = tuple(sorted({row.item_code for row in analyses}))
    matched = set(match_goal_items(goal, known_items))

    findings = tuple(
        PlanFinding(
            item_code=row.item_code,
            risk=row.risk,
            recommendation=_recommendation(row, row.item_code in matched),
            evidence=_evidence(row),
            matched_goal=row.item_code in matched,
        )
        for row in sorted(analyses, key=lambda row: (row.risk != SHORTAGE, row.item_code))
    )

    shortage = sum(1 for row in analyses if row.risk == SHORTAGE)
    duplicate = sum(1 for row in analyses if row.risk == DUPLICATE_RISK)
    needs_input = sum(1 for row in analyses if row.risk in {NEEDS_INPUT, UNKNOWN})
    summary = (
        f"共分析 {len(analyses)} 个物料：{shortage} 个缺货、{duplicate} 个重复采购风险、"
        f"{needs_input} 个输入不足；详情见逐项说明。"
    )
    return PlanResult(
        goal=goal,
        horizon_days=horizon_days,
        company=company,
        warehouse=warehouse,
        summary=summary,
        findings=findings,
        generated_at=generated_at or datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
    )
