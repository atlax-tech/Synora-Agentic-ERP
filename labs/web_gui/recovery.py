"""Small, deterministic recovery guards used by browser experiments."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from queue import Empty, Queue
from threading import Thread
from time import monotonic
from typing import Any, cast


class RecoveryFailure(RuntimeError):
    """A bounded wait or progress guard reached a safe stop."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def run_with_deadline[T](call: Callable[[], T], timeout_seconds: float) -> T:
    """Run an injected model call with a real caller-side deadline.

    The worker is daemonized because Python cannot safely kill an arbitrary
    provider call.  The task returns at the deadline and closes its browser;
    the isolated worker receives only the already-redacted observation.
    """

    if timeout_seconds <= 0:
        raise RecoveryFailure("MODEL_TIMEOUT")
    result: Queue[tuple[str, object]] = Queue(maxsize=1)

    def worker() -> None:
        try:
            result.put(("ok", call()))
        except BaseException as error:
            result.put(("error", error))

    thread = Thread(target=worker, daemon=True, name="phase11-model-call")
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise RecoveryFailure("MODEL_TIMEOUT")
    try:
        kind, value = result.get_nowait()
    except Empty as error:  # pragma: no cover - defensive worker contract
        raise RecoveryFailure("MODEL_CALL_FAILED") from error
    if kind == "error":
        if isinstance(value, RecoveryFailure):
            raise value
        code = getattr(value, "code", None)
        if isinstance(code, str) and code:
            raise RecoveryFailure(code)
        if isinstance(value, BaseException):
            raise RecoveryFailure("MODEL_CALL_FAILED") from value
        raise RecoveryFailure("MODEL_CALL_FAILED")
    return cast(T, value)


def remaining_timeout_ms(
    started: float, *, wall_time_seconds: float, action_timeout_seconds: float
) -> int:
    """Return one finite operation timeout bounded by the trial wall clock."""

    remaining = wall_time_seconds - (monotonic() - started)
    if remaining <= 0:
        raise RecoveryFailure("WALL_TIME_BUDGET")
    return max(1, int(min(action_timeout_seconds, remaining) * 1000))


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


__all__ = [
    "ProgressGuard",
    "RecoveryFailure",
    "remaining_timeout_ms",
    "run_with_deadline",
    "wait_for_ready",
]
