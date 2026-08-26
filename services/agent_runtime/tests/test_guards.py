"""P4.4 pure guard tests: canonical repeats, frequency, progress, and CAS."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from agent_runtime.agent.contracts import (
    Action,
    JsonValue,
    Observation,
    ToolName,
    observation_from_summary,
)
from agent_runtime.agent.guards import (
    NoProgressGuard,
    RepeatedCallGuard,
    RunConcurrencyRegistry,
    ToolFrequencyGuard,
)

RUN_ID = UUID("37e1d8a5-1730-4ad0-bffd-217774ed9fab")
CORRELATION_ID = UUID("1687c82a-4b61-4e6e-855a-a10ec3578b96")


def _action(
    *,
    tool_name: ToolName = "item.lookup",
    args: dict[str, JsonValue] | None = None,
) -> Action:
    return Action(
        step=1,
        tool_name=tool_name,
        canonical_args=args or {"query": "bearing"},
        correlation_id=CORRELATION_ID,
    )


def _observation(summary: str, *, ok: bool = True) -> Observation:
    return observation_from_summary(
        run_id=RUN_ID,
        step=1,
        tool_name="item.lookup",
        ok=ok,
        summary=summary,
        error_code=None if ok else "TOOL_FAILED",
        retryable=not ok,
    )


def test_repeated_call_uses_tool_and_canonical_args_only() -> None:
    guard = RepeatedCallGuard()

    assert guard.check(_action(args={"a": 1, "b": 2})) is False
    # JSON key order is not a semantic difference.
    assert guard.check(_action(args={"b": 2, "a": 1})) is True
    assert guard.check(_action(args={"a": 1, "b": 3})) is False
    assert guard.check(_action(tool_name="supplier.lookup", args={"a": 1, "b": 2})) is False


def test_no_progress_counts_repeated_digest_and_resets_on_new_digest() -> None:
    guard = NoProgressGuard(threshold=2)
    first = _observation("same")
    second = _observation("same")
    third = _observation("same")
    new = _observation("new")

    assert guard.check(first) is False
    assert guard.stale_count == 0
    assert guard.check(second) is False
    assert guard.stale_count == 1
    assert guard.check(third) is True
    assert guard.stale_count == 2
    assert guard.check(new) is False
    assert guard.stale_count == 0


def test_no_progress_also_tracks_failed_observations_by_digest() -> None:
    guard = NoProgressGuard(threshold=2)

    assert guard.check(_observation("gateway failed", ok=False)) is False
    assert guard.check(_observation("gateway failed", ok=False)) is False
    assert guard.check(_observation("gateway failed", ok=False)) is True


def test_tool_frequency_rejects_fourth_call_but_not_other_tools() -> None:
    guard = ToolFrequencyGuard(max_calls_per_tool=3)

    for index in range(3):
        assert guard.check(_action(args={"query": str(index)})) is False
    assert guard.check(_action(args={"query": "fourth"})) is True
    assert guard.count("item.lookup") == 4
    assert guard.check(_action(tool_name="supplier.lookup", args={"query": "same"})) is False


def test_run_concurrency_registry_is_atomic_for_one_run() -> None:
    registry = RunConcurrencyRegistry()
    run_id = RUN_ID

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(registry.try_acquire, [run_id] * 8))

    assert sum(claims) == 1
    assert registry.is_active(run_id) is True
    registry.release(run_id)
    assert registry.is_active(run_id) is False
    assert registry.try_acquire(run_id) is True
