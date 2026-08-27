"""Deterministic ProposedAction state machine with compare-and-set semantics."""

from __future__ import annotations

from synora_agentic_erp.gateway.contract import GatewayFault

ACTION_STATES: tuple[str, ...] = (
    "DRAFT",
    "INVALID",
    "POLICY_REJECTED",
    "AWAITING_APPROVAL",
    "APPROVED",
    "DECLINED",
    "EXPIRED",
    "EXECUTED",
)

_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"INVALID", "POLICY_REJECTED", "AWAITING_APPROVAL"}),
    "INVALID": frozenset(),
    "POLICY_REJECTED": frozenset(),
    "AWAITING_APPROVAL": frozenset({"APPROVED", "DECLINED", "EXPIRED"}),
    "APPROVED": frozenset({"EXECUTED", "EXPIRED"}),
    "DECLINED": frozenset(),
    "EXPIRED": frozenset(),
    "EXECUTED": frozenset(),
}


def validate_transition(current: str, target: str) -> None:
    if current not in _TRANSITIONS or target not in ACTION_STATES:
        raise GatewayFault("CONFLICT", "governed action state is invalid", 409)
    if target not in _TRANSITIONS[current]:
        raise GatewayFault("CONFLICT", "governed action state transition is not allowed", 409)


def transition_state(
    current: str,
    target: str,
    *,
    state_version: int,
    expected_version: int | None = None,
) -> tuple[str, int]:
    if isinstance(state_version, bool) or not isinstance(state_version, int) or state_version < 1:
        raise GatewayFault("CONFLICT", "governed action state version is invalid", 409)
    if expected_version is not None and expected_version != state_version:
        raise GatewayFault("CONFLICT", "governed action state changed concurrently", 409)
    validate_transition(current, target)
    return target, state_version + 1
