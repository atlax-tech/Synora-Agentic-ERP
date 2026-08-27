"""确定性 Agent Run 生命周期状态机 (SPEC §8.1)。

纯函数模块, 不依赖 frappe: 任何状态转换必须先通过 validate_transition,
非法转换 fail closed (GatewayFault CONFLICT / INVALID_TRANSITION)。
模型只可建议下一步, 不可直接设置状态。
"""

from synora_agentic_erp.gateway.contract import GatewayFault

RUN_STATES: tuple[str, ...] = (
    "CREATED",
    "ANALYZING",
    "PROPOSED",
    "AWAITING_APPROVAL",
    "EXECUTING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "RECONCILIATION_REQUIRED",
    "DECLINED",
    "EXPIRED",
)

# SPEC §8.1 状态机转换表: current -> 允许的 targets。
# 验收修复: ANALYZING 允许回退 CREATED —— 分析中途工具/数据失败时恢复可重试
# (不再留下永久中间态与部分分析记录; 回退由受控路径执行, 失败才允许)。
_TRANSITIONS: dict[str, frozenset[str]] = {
    "CREATED": frozenset({"ANALYZING", "CANCELLED", "EXPIRED"}),
    "ANALYZING": frozenset({"PROPOSED", "FAILED", "CANCELLED", "CREATED", "EXPIRED"}),
    "PROPOSED": frozenset({"AWAITING_APPROVAL", "SUCCEEDED"}),
    "AWAITING_APPROVAL": frozenset({"DECLINED", "EXPIRED", "EXECUTING"}),
    "EXECUTING": frozenset({"SUCCEEDED", "FAILED", "RECONCILIATION_REQUIRED"}),
    "RECONCILIATION_REQUIRED": frozenset({"SUCCEEDED", "FAILED"}),
    "SUCCEEDED": frozenset(),
    "FAILED": frozenset(),
    "CANCELLED": frozenset(),
    "DECLINED": frozenset(),
    "EXPIRED": frozenset(),
}

# 允许用户主动取消的状态 (发起人取消分析)。
CANCELLABLE_STATES: frozenset[str] = frozenset({"CREATED", "ANALYZING"})


def validate_transition(current: str, target: str) -> None:
    """校验 current -> target 是否合法; 非法直接抛 GatewayFault。"""
    if current not in _TRANSITIONS:
        raise GatewayFault("INVALID_TRANSITION", "run state is invalid", 409)
    if target not in _TRANSITIONS[current]:
        raise GatewayFault("CONFLICT", "run state transition is not allowed", 409)
