"""Pure Memory lifecycle transitions with compare-and-set semantics.

This module deliberately has no persistence, Frappe, HTTP, or permission
side effects.  Storage and authorization layers must call these functions
before applying a state change to a durable record.
"""

from __future__ import annotations

from typing import cast

from agent_runtime.memory.contracts import MEMORY_STATES, MemoryState


class MemoryStateError(ValueError):
    """Deterministic Memory lifecycle error without transport coupling."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


_TRANSITIONS: dict[MemoryState, frozenset[MemoryState]] = {
    "CANDIDATE": frozenset({"APPROVED", "REJECTED", "EXPIRED", "DELETED"}),
    "APPROVED": frozenset({"SUPERSEDED", "EXPIRED", "DELETED"}),
    "REJECTED": frozenset({"DELETED"}),
    "SUPERSEDED": frozenset({"DELETED"}),
    "EXPIRED": frozenset({"DELETED"}),
    "DELETED": frozenset(),
}


def _validate_version(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MemoryStateError("INVALID_VERSION", f"{field_name} must be an integer >= 1")
    return value


def validate_transition(current: str, target: str) -> None:
    """Raise when ``current -> target`` is not an allowed Memory transition."""

    if current not in MEMORY_STATES:
        raise MemoryStateError("INVALID_STATE", "current Memory state is invalid")
    if target not in MEMORY_STATES:
        raise MemoryStateError("INVALID_STATE", "target Memory state is invalid")

    current_state = cast(MemoryState, current)
    target_state = cast(MemoryState, target)
    if target_state not in _TRANSITIONS[current_state]:
        raise MemoryStateError("INVALID_TRANSITION", "Memory state transition is not allowed")


def transition_state(
    current: str,
    target: str,
    *,
    state_version: int,
    expected_version: int | None = None,
) -> tuple[MemoryState, int]:
    """Return the next state/version when the transition passes the CAS gate."""

    current_version = _validate_version(state_version, "state_version")
    if expected_version is not None:
        expected = _validate_version(expected_version, "expected_version")
        if expected != current_version:
            raise MemoryStateError("STALE_VERSION", "Memory state changed concurrently")
    validate_transition(current, target)
    return cast(MemoryState, target), current_version + 1


__all__ = ["MemoryStateError", "transition_state", "validate_transition"]
