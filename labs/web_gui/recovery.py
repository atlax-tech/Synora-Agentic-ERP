"""Small, deterministic recovery guards used by browser experiments."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class RecoveryFailure(RuntimeError):
    """A bounded wait or progress guard reached a safe stop."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def wait_for_ready(page: Any, *, timeout_ms: float, scenario: str) -> None:
    """Wait for an observable ready marker only when a fixture needs one."""

    if scenario not in {"async", "timeout"}:
        return
    try:
        page.wait_for_selector('[data-state="ready"]', state="attached", timeout=timeout_ms)
    except Exception as error:
        raise RecoveryFailure("PAGE_NOT_READY") from error


@dataclass
class ProgressGuard:
    """Stop an unchanged action/observation pair after its first repeat."""

    max_actions: int
    actions: int = 0
    _seen: set[tuple[str, str]] = field(default_factory=set)

    def record(self, observation_digest: str, action_digest: str) -> None:
        if self.actions >= self.max_actions:
            raise RecoveryFailure("ACTION_BUDGET")
        signature = (observation_digest, action_digest)
        if signature in self._seen:
            raise RecoveryFailure("NO_PROGRESS")
        self._seen.add(signature)
        self.actions += 1


__all__ = ["ProgressGuard", "RecoveryFailure", "wait_for_ready"]
