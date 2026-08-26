"""Small, reusable stop guards for the bounded Phase 4 execution kernel."""

from __future__ import annotations

from threading import Lock
from uuid import UUID

from agent_runtime.agent.contracts import Action, Observation


class RepeatedCallGuard:
    """Reject the second call with the same canonical tool and arguments."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def check(self, action: Action) -> bool:
        key = action.call_key()
        if key in self._seen:
            return True
        self._seen.add(key)
        return False


class ToolFrequencyGuard:
    """Bound how often one canonical tool can be called in a single run."""

    def __init__(self, *, max_calls_per_tool: int = 3) -> None:
        if max_calls_per_tool < 1:
            raise ValueError("max_calls_per_tool must be positive")
        self._max_calls_per_tool = max_calls_per_tool
        self._counts: dict[str, int] = {}

    def check(self, action: Action) -> bool:
        count = self._counts.get(action.tool_name, 0) + 1
        self._counts[action.tool_name] = count
        return count > self._max_calls_per_tool

    def count(self, tool_name: str) -> int:
        return self._counts.get(tool_name, 0)


class NoProgressGuard:
    """Stop after a configured run of observations with no new digest."""

    def __init__(self, *, threshold: int = 2) -> None:
        if threshold < 1:
            raise ValueError("no-progress threshold must be positive")
        self._threshold = threshold
        self._seen_digests: set[str] = set()
        self._stale_count = 0

    def check(self, observation: Observation) -> bool:
        if observation.digest in self._seen_digests:
            self._stale_count += 1
        else:
            self._seen_digests.add(observation.digest)
            self._stale_count = 0
        return self._stale_count >= self._threshold

    @property
    def stale_count(self) -> int:
        return self._stale_count


class RunConcurrencyRegistry:
    """Process-local CAS gate used before a run can call a provider."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._active: set[UUID] = set()

    def try_acquire(self, run_id: UUID) -> bool:
        """Atomically claim a Run; a second claimant loses without billing."""
        with self._lock:
            if run_id in self._active:
                return False
            self._active.add(run_id)
            return True

    def release(self, run_id: UUID) -> None:
        with self._lock:
            self._active.discard(run_id)

    def is_active(self, run_id: UUID) -> bool:
        with self._lock:
            return run_id in self._active
