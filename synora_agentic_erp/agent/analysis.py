"""确定性采购风险分析 (PRD F-003)。

纯函数模块, 不依赖 frappe: 库存公式、数量、日期比较和阈值判断全部由
确定性代码完成, LLM 不参与任何数量/金额/阈值计算。同一固定输入必须
产生同一结果; 无法取得必需输入时返回 NEEDS_INPUT/UNKNOWN, 不做估算。

数量语义 (来自 Phase 2 工具契约, 均已折算为 stock_uom):
- actual_qty: 当前实际库存 (Bin.actual_qty 汇总);
- demand_lines: 未结需求行 (未结 MR outstanding, 含日期);
- incoming_lines: 在途供应行 (未收货 PO outstanding, 含日期);
- open_mr_qty: 该 item 未结 MR 全部 outstanding (含窗口外);
- horizon: 时间窗口截止日 (today + time_window_days, P3.1 批准缺省 90 天)。

净位置 = actual_qty + 窗口内在途 - 窗口内需求; 与 ERPNext projected_qty
(Bin 含 indented/ordered) 的差异在于: 分析按 schedule_date 过滤窗口,
避免把窗口外需求提前计入缺货判定。
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

SHORTAGE = "SHORTAGE"
ADEQUATE = "ADEQUATE"
DUPLICATE_RISK = "DUPLICATE_RISK"
NO_DEMAND = "NO_DEMAND"
NEEDS_INPUT = "NEEDS_INPUT"
UNKNOWN = "UNKNOWN"

RISKS: tuple[str, ...] = (
    SHORTAGE,
    ADEQUATE,
    DUPLICATE_RISK,
    NO_DEMAND,
    NEEDS_INPUT,
    UNKNOWN,
)

_ZERO = Decimal("0")


@dataclass(frozen=True)
class DemandLine:
    qty: Decimal
    schedule_date: date


@dataclass(frozen=True)
class IncomingLine:
    qty: Decimal
    schedule_date: date


@dataclass(frozen=True)
class ItemInput:
    item_code: str
    actual_qty: Decimal | None
    horizon: date
    demand_lines: tuple[DemandLine, ...] = ()
    incoming_lines: tuple[IncomingLine, ...] = ()
    open_mr_qty: Decimal | None = None


@dataclass(frozen=True)
class ItemAnalysis:
    item_code: str
    risk: str
    actual_qty: Decimal
    demand_qty: Decimal
    incoming_qty: Decimal
    open_mr_qty: Decimal
    net_position: Decimal
    shortage_qty: Decimal
    unknowns: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "item_code": self.item_code,
            "risk": self.risk,
            "actual_qty": format(self.actual_qty, "f"),
            "demand_qty": format(self.demand_qty, "f"),
            "incoming_qty": format(self.incoming_qty, "f"),
            "open_mr_qty": format(self.open_mr_qty, "f"),
            "net_position": format(self.net_position, "f"),
            "shortage_qty": format(self.shortage_qty, "f"),
            "unknowns": list(self.unknowns),
        }


def horizon_date(today: date, time_window_days: int) -> date:
    """时间窗口截止日: 缺省语义为当前库存 + 在途 + 未来 N 天需求。"""
    return today + timedelta(days=time_window_days)


def _negative_unknowns(inp: ItemInput) -> list[str]:
    unknown: list[str] = []
    if inp.actual_qty is not None and inp.actual_qty < _ZERO:
        unknown.append("negative_actual_qty")
    if any(line.qty < _ZERO for line in inp.demand_lines):
        unknown.append("negative_demand_qty")
    if any(line.qty < _ZERO for line in inp.incoming_lines):
        unknown.append("negative_incoming_qty")
    if inp.open_mr_qty is not None and inp.open_mr_qty < _ZERO:
        unknown.append("negative_open_mr_qty")
    return unknown


def analyze_item(inp: ItemInput) -> ItemAnalysis:
    """对单个 item 做确定性风险判定; 同一输入恒得同一结果。"""
    item_code = inp.item_code
    conflict = _negative_unknowns(inp)
    if conflict:
        return ItemAnalysis(
            item_code=item_code,
            risk=UNKNOWN,
            actual_qty=inp.actual_qty if inp.actual_qty is not None else _ZERO,
            demand_qty=_ZERO,
            incoming_qty=_ZERO,
            open_mr_qty=inp.open_mr_qty if inp.open_mr_qty is not None else _ZERO,
            net_position=_ZERO,
            shortage_qty=_ZERO,
            unknowns=tuple(conflict),
        )

    missing: list[str] = []
    if inp.actual_qty is None:
        missing.append("actual_qty")
    if inp.open_mr_qty is None:
        missing.append("open_mr_qty")
    if missing:
        # 必需输入缺失: 明确返回 NEEDS_INPUT, 不估算。
        return ItemAnalysis(
            item_code=item_code,
            risk=NEEDS_INPUT,
            actual_qty=_ZERO,
            demand_qty=_ZERO,
            incoming_qty=_ZERO,
            open_mr_qty=_ZERO,
            net_position=_ZERO,
            shortage_qty=_ZERO,
            unknowns=tuple(missing),
        )

    # 缺失分支已返回, 此处 actual_qty / open_mr_qty 必然存在。
    actual_qty = inp.actual_qty
    open_mr_qty = inp.open_mr_qty
    assert actual_qty is not None and open_mr_qty is not None

    demand_in_window = sum(
        (line.qty for line in inp.demand_lines if line.schedule_date <= inp.horizon), _ZERO
    )
    incoming_in_window = sum(
        (line.qty for line in inp.incoming_lines if line.schedule_date <= inp.horizon), _ZERO
    )
    net_position = actual_qty + incoming_in_window - demand_in_window

    if demand_in_window == _ZERO and incoming_in_window == _ZERO:
        risk = NO_DEMAND
        shortage = _ZERO
    elif net_position < _ZERO:
        risk = SHORTAGE
        shortage = -net_position
    elif open_mr_qty > _ZERO or incoming_in_window > _ZERO:
        # 已有需求计划 (未结 MR) 或在途供应 (PO) 覆盖, 再采购即重复。
        risk = DUPLICATE_RISK
        shortage = _ZERO
    else:
        risk = ADEQUATE
        shortage = _ZERO

    return ItemAnalysis(
        item_code=item_code,
        risk=risk,
        actual_qty=actual_qty,
        demand_qty=demand_in_window,
        incoming_qty=incoming_in_window,
        open_mr_qty=open_mr_qty,
        net_position=net_position,
        shortage_qty=shortage,
        unknowns=(),
    )
