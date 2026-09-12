"""Evaluation runners, provider budgets, and held-out-safe scoring."""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from agent_runtime.providers import ProviderError, ProviderMessage

from .contracts import DatasetCase, DatasetManifest, ExperimentRecord, digest_json
from .data import model_input_text
from .replay import (
    READ_ACTIONS,
    Policy,
    ReplayResult,
    ReplayState,
    deterministic_policy,
    run_replay,
)

MAX_INPUT_CHARS = 4_000
MAX_OUTPUT_TOKENS = 512
MAX_OUTPUT_CHARS = MAX_OUTPUT_TOKENS * 4
MAX_CALL_SECONDS = 60.0
MAX_MODEL_CALLS = 1_200
RESERVATION_LEDGER_NAME = "live-reservations.jsonl"
_BATCH_BLOCKING_FAILURES = frozenset(
    {
        "AUTHENTICATION_ERROR",
        "AUTH_FAILED",
        "CONNECTION_ERROR",
        "NETWORK_ERROR",
        "PROTOCOL_ERROR",
        "PROVIDER_UNAVAILABLE",
        "TRANSPORT_ERROR",
        "TIMEOUT",
        "MODEL_CALL_TIMEOUT",
    }
)


class BudgetExceeded(RuntimeError):
    """No provider call is made after the cumulative budget is exhausted."""


_LEDGER_STATES = frozenset({"RESERVED", "COMPLETED", "FAILED", "UNKNOWN", "RECORDED"})


@dataclass
class ReservationLedger:
    """Append-only provider reservation state shared by live processes."""

    path: Path

    def __post_init__(self) -> None:
        if self.path.exists() and self.path.is_symlink():
            raise ValueError("reservation ledger cannot be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._states = self._read()

    def _read(self) -> dict[str, tuple[str, str]]:
        if not self.path.exists():
            return {}
        if not self.path.is_file() or self.path.is_symlink():
            raise ValueError("reservation ledger must be a regular file")
        states: dict[str, tuple[str, str]] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict) or set(payload) != {
                "schema_version",
                "batch_id",
                "reservation_key",
                "state",
            }:
                raise ValueError("reservation ledger entry is invalid")
            if payload["schema_version"] != "1":
                raise ValueError("reservation ledger schema is unsupported")
            batch_id = payload["batch_id"]
            key = payload["reservation_key"]
            state = payload["state"]
            if (
                not isinstance(batch_id, str)
                or not batch_id
                or len(batch_id) > 120
                or not isinstance(key, str)
                or not key
                or len(key) > 240
                or not isinstance(state, str)
                or state not in _LEDGER_STATES
            ):
                raise ValueError("reservation ledger values are invalid")
            previous = states.get(key)
            if previous is not None and previous[1] == "RECORDED":
                raise ValueError("reservation ledger contains an event after RECORDED")
            if previous is not None and previous[0] != batch_id:
                raise ValueError("reservation key is bound to multiple batches")
            if previous is not None:
                if previous[1] != "RESERVED" and state != "RECORDED":
                    raise ValueError("reservation has more than one terminal event")
                if previous[1] == "RESERVED" and state == "RECORDED":
                    raise ValueError("reservation must have a terminal event first")
                if state == "RESERVED":
                    raise ValueError("reservation cannot return to RESERVED")
            states[key] = (batch_id, state)
        return states

    @property
    def reservation_count(self) -> int:
        return len(self._states)

    @property
    def unmaterialized_count(self) -> int:
        return sum(state != "RECORDED" for _, state in self._states.values())

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self._states)

    def states(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._states.items())

    def _append(self, batch_id: str, reservation_key: str, state: str) -> None:
        payload = {
            "schema_version": "1",
            "batch_id": batch_id,
            "reservation_key": reservation_key,
            "state": state,
        }
        encoded = (json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n").encode("utf-8")
        if self.path.is_symlink():
            raise ValueError("reservation ledger cannot be a symlink")
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def reserve(self, batch_id: str, reservation_key: str) -> None:
        if not batch_id or len(batch_id) > 120 or "\n" in batch_id:
            raise ValueError("batch id is invalid")
        if not reservation_key or len(reservation_key) > 240 or "\n" in reservation_key:
            raise ValueError("reservation key is invalid")
        if reservation_key in self._states:
            raise BudgetExceeded("CALL_RESERVATION_EXISTS")
        self._append(batch_id, reservation_key, "RESERVED")
        self._states[reservation_key] = (batch_id, "RESERVED")

    def finish(self, reservation_key: str, state: str) -> None:
        if state not in {"COMPLETED", "FAILED", "UNKNOWN"}:
            raise ValueError("reservation terminal state is invalid")
        previous = self._states.get(reservation_key)
        if previous is None:
            raise ValueError("reservation key is unknown")
        if previous[1] != "RESERVED":
            raise ValueError("reservation is already finalized")
        self._append(previous[0], reservation_key, state)
        self._states[reservation_key] = (previous[0], state)

    def mark_recorded(self, reservation_keys: Iterable[str]) -> None:
        for reservation_key in reservation_keys:
            previous = self._states.get(reservation_key)
            if previous is None:
                raise ValueError("reservation key is unknown")
            if previous[1] == "RECORDED":
                continue
            if previous[1] not in {"COMPLETED", "FAILED", "UNKNOWN"}:
                raise ValueError("reservation must be finalized before recording")
            self._append(previous[0], reservation_key, "RECORDED")
            self._states[reservation_key] = (previous[0], "RECORDED")

    def mark_recorded_matching(self, suffixes: Iterable[str]) -> None:
        wanted = tuple(suffixes)
        self.mark_recorded(
            key
            for key, (_, state) in self._states.items()
            if state != "RECORDED" and any(key.endswith(suffix) for suffix in wanted)
        )


@dataclass
class CallBudget:
    maximum: int = MAX_MODEL_CALLS
    used: int = 0
    consecutive_blocking_failures: int = 0
    blocked: bool = False
    ledger: ReservationLedger | None = None
    batch_id: str = "phase12-default-batch"

    def __post_init__(self) -> None:
        if self.maximum < 0 or self.maximum > MAX_MODEL_CALLS:
            raise ValueError("budget maximum is outside the Phase 12 limit")
        if self.used < 0 or self.used > self.maximum:
            raise ValueError("budget used count is outside the configured limit")
        if self.consecutive_blocking_failures < 0:
            raise ValueError("blocking failure count cannot be negative")
        if not self.batch_id or len(self.batch_id) > 120 or "\n" in self.batch_id:
            raise ValueError("batch id is invalid")
        if self.ledger is not None:
            self.used += self.ledger.unmaterialized_count
            if self.used > self.maximum:
                raise ValueError("ledger reservations exceed the configured budget")

    def reserve(self, reservation_key: str | None = None) -> int:
        if self.blocked:
            raise BudgetExceeded("MODEL_BATCH_BLOCKED")
        if self.used >= self.maximum:
            raise BudgetExceeded("MODEL_CALL_BUDGET")
        key = reservation_key or f"{self.batch_id}:call-{self.used + 1}"
        if self.ledger is not None:
            self.ledger.reserve(self.batch_id, key)
        self.used += 1
        return self.used

    def finish(self, reservation_key: str | None, state: str) -> None:
        if self.ledger is not None and reservation_key is not None:
            self.ledger.finish(reservation_key, state)

    def note(self, failure_code: str | None) -> None:
        """Stop a batch after three consecutive connection/auth/protocol failures."""
        if failure_code in _BATCH_BLOCKING_FAILURES:
            self.consecutive_blocking_failures += 1
            if self.consecutive_blocking_failures >= 3:
                self.blocked = True
            return
        self.consecutive_blocking_failures = 0


@dataclass(frozen=True)
class LiveCall:
    action: str | None
    status: str
    failure_code: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    elapsed_ms: float
    attempted: bool = False


@dataclass(frozen=True)
class CandidateOutcome:
    index: int
    action: str
    result: ReplayResult


@dataclass(frozen=True)
class MethodOutcome:
    method: str
    result: ReplayResult
    calls: int
    improved: bool


@dataclass(frozen=True)
class BootstrapSummary:
    method_a: str
    method_b: str
    groups: int
    delta: float
    lower_95: float
    upper_95: float
    conclusion: Literal["IMPROVED", "INCONCLUSIVE", "REGRESSED"]


class LiveProvider(Protocol):
    async def complete(self, messages: list[ProviderMessage], **kwargs: object) -> object: ...


def _parse_action(text: str) -> str:
    if len(text) > MAX_OUTPUT_CHARS:
        raise ValueError("MODEL_RESPONSE_TOO_LARGE")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("MODEL_RESPONSE_SCHEMA") from error
    if not isinstance(payload, dict) or set(payload) != {"action"}:
        raise ValueError("MODEL_RESPONSE_SCHEMA")
    action = payload.get("action")
    if not isinstance(action, str) or not action:
        raise ValueError("MODEL_RESPONSE_SCHEMA")
    return action


async def _call_provider(
    provider: LiveProvider,
    prompt: str,
    budget: CallBudget,
    *,
    reservation_key: str | None = None,
) -> LiveCall:
    if len(prompt) > MAX_INPUT_CHARS:
        return LiveCall(None, "REJECTED", "INPUT_TOO_LARGE", None, None, 0.0)
    key = reservation_key or f"{budget.batch_id}:call-{budget.used + 1}"
    try:
        budget.reserve(key)
    except BudgetExceeded as error:
        return LiveCall(None, "UNKNOWN", str(error), None, None, 0.0)
    start = time.perf_counter()
    try:
        response = await asyncio.wait_for(
            provider.complete(
                [ProviderMessage(role="user", content=prompt)],
                tools=[],
                max_tokens=MAX_OUTPUT_TOKENS,
            ),
            timeout=MAX_CALL_SECONDS,
        )
    except ProviderError as error:
        call = LiveCall(
            None,
            "UNKNOWN",
            error.failure_code,
            error.prompt_tokens or None,
            error.completion_tokens or None,
            (time.perf_counter() - start) * 1000,
            True,
        )
        budget.finish(key, "UNKNOWN")
        budget.note(call.failure_code)
        return call
    except TimeoutError:
        call = LiveCall(
            None,
            "UNKNOWN",
            "MODEL_CALL_TIMEOUT",
            None,
            None,
            (time.perf_counter() - start) * 1000,
            True,
        )
        budget.finish(key, "UNKNOWN")
        budget.note(call.failure_code)
        return call
    except OSError as error:
        failure_code = (
            "CONNECTION_ERROR"
            if isinstance(error, ConnectionError)
            else type(error).__name__.upper()
        )
        call = LiveCall(
            None,
            "UNKNOWN",
            failure_code,
            None,
            None,
            (time.perf_counter() - start) * 1000,
            True,
        )
        budget.finish(key, "UNKNOWN")
        budget.note(call.failure_code)
        return call
    elapsed = (time.perf_counter() - start) * 1000
    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text:
        call = LiveCall(None, "UNKNOWN", "MODEL_RESPONSE_EMPTY", None, None, elapsed, True)
        budget.finish(key, "UNKNOWN")
        budget.note(call.failure_code)
        return call
    try:
        action = _parse_action(text)
    except ValueError as error:
        call = LiveCall(None, "FAILED", str(error), None, None, elapsed, True)
        budget.finish(key, "FAILED")
        budget.note(call.failure_code)
        return call
    prompt_tokens = getattr(response, "prompt_tokens", 0)
    completion_tokens = getattr(response, "completion_tokens", 0)
    known_usage = isinstance(prompt_tokens, int) and isinstance(completion_tokens, int)
    call = LiveCall(
        action,
        "SUCCEEDED",
        None,
        prompt_tokens if known_usage and prompt_tokens > 0 else None,
        completion_tokens if known_usage and completion_tokens > 0 else None,
        elapsed,
        True,
    )
    budget.finish(key, "COMPLETED")
    budget.note(None)
    return call


def run_live_baseline(
    case: DatasetCase,
    provider: LiveProvider,
    budget: CallBudget,
    *,
    code_version: str,
    model: str,
    repeat: int = 1,
    dataset_id: str = "phase12-synthetic-v1",
    dataset_digest: str | None = None,
) -> ExperimentRecord:
    """Run one bounded model decision and pass it through the local verifier."""
    prompt = json.dumps(
        {
            "task": model_input_text(case),
            "rules": [
                "Use one bounded read-only action.",
                'Return JSON: {"action": "..."}.',
                "Preserve unknowns.",
            ],
            "allowed_actions": [*sorted(READ_ACTIONS), "ASK_INPUT", "FINISH"],
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    reservation_key = f"{budget.batch_id}:repeat:{repeat}:case:{case.case_id}"
    call = asyncio.run(_call_provider(provider, prompt, budget, reservation_key=reservation_key))
    return _live_record(case, call, code_version, model, repeat, dataset_id, dataset_digest)


def _live_record(
    case: DatasetCase,
    call: LiveCall,
    code_version: str,
    model: str,
    repeat: int,
    dataset_id: str,
    dataset_digest: str | None,
) -> ExperimentRecord:
    """Build one immutable result after the provider call has completed."""
    bound_digest = dataset_digest or digest_json(case.model_dump(mode="json"))
    if call.action is None:
        return ExperimentRecord(
            experiment_id=f"phase12-exp-live-{case.case_id}-{repeat}",
            code_version=code_version,
            dataset_id=dataset_id,
            dataset_digest=bound_digest,
            split=case.split,
            method="baseline",
            model=model,
            repeat=repeat,
            status=call.status,  # type: ignore[arg-type]
            verifier_passed=False,
            safety_passed=call.status != "REJECTED",
            score=-1.0,
            failure_code=call.failure_code,
            prompt_tokens=call.prompt_tokens,
            completion_tokens=call.completion_tokens,
            elapsed_ms=call.elapsed_ms,
            calls=int(call.attempted),
        )
    result = run_replay(case, lambda _state: call.action or "FINISH")
    status: Literal["SUCCEEDED", "REJECTED"] = "SUCCEEDED" if result.verifier_passed else "REJECTED"
    return ExperimentRecord(
        experiment_id=f"phase12-exp-live-{case.case_id}-{repeat}",
        code_version=code_version,
        dataset_id=dataset_id,
        dataset_digest=bound_digest,
        split=case.split,
        method="baseline",
        model=model,
        repeat=repeat,
        status=status,
        output_action=call.action,
        verifier_passed=result.verifier_passed,
        safety_passed=result.safety_passed,
        score=result.score,
        failure_code=None if result.verifier_passed else result.failure_code,
        prompt_tokens=call.prompt_tokens,
        completion_tokens=call.completion_tokens,
        elapsed_ms=call.elapsed_ms,
        calls=int(call.attempted),
    )


def run_live_baselines(
    cases: Iterable[DatasetCase],
    provider: LiveProvider,
    budget: CallBudget,
    *,
    code_version: str,
    model: str,
    repeats: int = 1,
    dataset_id: str = "phase12-synthetic-v1",
    dataset_digest: str | None = None,
) -> tuple[ExperimentRecord, ...]:
    """Run a batch on one event loop so async providers retain their client state."""
    if repeats < 1 or repeats > 3:
        raise ValueError("live repeats must be between one and three")
    values = tuple(cases)

    async def execute() -> tuple[ExperimentRecord, ...]:
        records: list[ExperimentRecord] = []
        for repeat in range(1, repeats + 1):
            for case in values:
                prompt = json.dumps(
                    {
                        "task": model_input_text(case),
                        "rules": [
                            "Use one bounded read-only action.",
                            '{"action": "..."} is the only output shape.',
                            "Preserve unknowns.",
                        ],
                        "allowed_actions": [*sorted(READ_ACTIONS), "ASK_INPUT", "FINISH"],
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
                reservation_key = f"{budget.batch_id}:repeat:{repeat}:case:{case.case_id}"
                call = await _call_provider(
                    provider, prompt, budget, reservation_key=reservation_key
                )
                records.append(
                    _live_record(
                        case,
                        call,
                        code_version,
                        model,
                        repeat,
                        dataset_id,
                        dataset_digest,
                    )
                )
        return tuple(records)

    return asyncio.run(execute())


def evaluate_replay_cases(
    cases: Iterable[DatasetCase],
    policy: Policy = deterministic_policy,
    *,
    code_version: str,
    model: str = "deterministic-replay",
    method: str = "baseline",
    dataset_id: str = "phase12-synthetic-v1",
    dataset_digest: str | None = None,
    repeat: int = 1,
    candidate_id: str | None = None,
) -> tuple[ExperimentRecord, ...]:
    if repeat < 1 or repeat > 20:
        raise ValueError("repeat must be between one and twenty")
    values = tuple(cases)
    bound_digest = dataset_digest or digest_json(
        {"cases": [case.model_dump(mode="json") for case in values]}
    )
    records: list[ExperimentRecord] = []
    for case in values:
        result: ReplayResult = run_replay(case, policy)
        records.append(
            ExperimentRecord(
                experiment_id=f"phase12-exp-replay-{method.lower()}-{repeat}-{case.case_id}",
                code_version=code_version,
                dataset_id=dataset_id,
                dataset_digest=bound_digest,
                split=case.split,
                method=method,
                candidate_id=candidate_id,
                model=model,
                repeat=repeat,
                status="SUCCEEDED" if result.verifier_passed else "REJECTED",
                output_action=result.action_sequence[-1] if result.action_sequence else None,
                verifier_passed=result.verifier_passed,
                safety_passed=result.safety_passed,
                score=result.score,
                failure_code=None if result.verifier_passed else result.failure_code,
                elapsed_ms=0.0,
                calls=0,
            )
        )
    return tuple(records)


def aggregate(records: Iterable[ExperimentRecord]) -> dict[str, float]:
    values = tuple(records)
    if not values:
        return {"count": 0.0, "verifier_rate": 0.0, "safety_rate": 0.0, "mean_score": 0.0}
    return {
        "count": float(len(values)),
        "verifier_rate": sum(record.verifier_passed for record in values) / len(values),
        "safety_rate": sum(record.safety_passed for record in values) / len(values),
        "mean_score": sum(record.score for record in values) / len(values),
    }


def reflection_replay(
    case: DatasetCase,
    first_policy: Policy,
    revision_policy: Policy,
) -> MethodOutcome:
    """Run one generation and at most one revision from observable feedback."""
    first = run_replay(case, first_policy)
    if _observable_candidate(first):
        return MethodOutcome("reflection", first, 1, False)
    revised = run_replay(case, revision_policy)
    return MethodOutcome("reflection", revised, 2, revised.verifier_passed)


def _observable_candidate(result: ReplayResult) -> bool:
    """Gate candidates without reading expected answer or oracle fields."""
    if (
        not result.safety_passed
        or not result.action_sequence
        or result.failure_code not in {None, "VERIFIER_MISMATCH"}
    ):
        return False
    terminal = result.action_sequence[-1]
    if terminal == "ASK_INPUT":
        return True
    return terminal == "FINISH" and bool(result.observations)


def rerank_candidates(
    case: DatasetCase,
    actions: Iterable[str],
) -> tuple[CandidateOutcome, ...]:
    """Apply hard safety/verifier gates before an evidence-only stable sort."""
    outcomes: list[CandidateOutcome] = []
    for index, action in enumerate(actions):

        def fixed_policy(_state: ReplayState, value: str = action) -> str:
            return value

        result = run_replay(case, fixed_policy)
        if _observable_candidate(result):
            outcomes.append(CandidateOutcome(index, action, result))
    outcomes.sort(key=lambda item: (-len(item.result.observations), item.result.steps, item.index))
    return tuple(outcomes)


def best_of_n_replay(
    case: DatasetCase,
    policies: Iterable[Policy],
    *,
    n: int = 3,
) -> MethodOutcome:
    """Evaluate at most n independent candidates and select only a valid one."""
    if n < 1 or n > 3:
        raise ValueError("Best-of-N is bounded to one through three candidates")
    results: list[ReplayResult] = []
    for policy in tuple(policies)[:n]:
        results.append(run_replay(case, policy))
    accepted = [result for result in results if _observable_candidate(result)]
    if accepted:
        selected = sorted(
            accepted,
            key=lambda result: (-len(result.observations), result.steps, results.index(result)),
        )[0]
    elif results:
        selected = results[0]
    else:
        selected = run_replay(case, lambda _state: "FINISH")
    return MethodOutcome("best_of_n", selected, len(results), selected.verifier_passed)


def held_out_replay(
    manifest: DatasetManifest,
    methods: dict[str, Policy],
    *,
    repeats: int = 1,
    code_version: str,
) -> tuple[ExperimentRecord, ...]:
    """Evaluate frozen methods on test cases without exposing their oracle."""
    if repeats < 1 or repeats > 3:
        raise ValueError("held-out repeats must be between one and three")
    cases = tuple(case for case in manifest.cases if case.split == "test")
    records: list[ExperimentRecord] = []
    for method, policy in methods.items():
        for repeat in range(1, repeats + 1):
            for case in cases:
                result = run_replay(case, policy)
                records.append(
                    ExperimentRecord(
                        experiment_id=f"phase12-exp-heldout-{method}-{repeat}-{case.case_id}",
                        code_version=code_version,
                        dataset_id=manifest.dataset_id,
                        dataset_digest=manifest.dataset_digest,
                        split="test",
                        method=method,
                        model="deterministic-replay",
                        repeat=repeat,
                        status="SUCCEEDED" if result.verifier_passed else "REJECTED",
                        output_action=result.action_sequence[-1]
                        if result.action_sequence
                        else None,
                        verifier_passed=result.verifier_passed,
                        safety_passed=result.safety_passed,
                        score=result.score,
                        failure_code=None if result.verifier_passed else result.failure_code,
                        elapsed_ms=0.0,
                        calls=0,
                    )
                )
    return tuple(records)


def grouped_bootstrap(
    records_a: Iterable[ExperimentRecord],
    records_b: Iterable[ExperimentRecord],
    *,
    method_a: str,
    method_b: str,
    seed: int = 12,
    samples: int = 2_000,
) -> BootstrapSummary:
    """Compute a paired group bootstrap over verifier rates."""
    if samples < 100:
        raise ValueError("bootstrap needs at least 100 samples")
    a_by_group: dict[str, list[bool]] = {}
    b_by_group: dict[str, list[bool]] = {}

    def record_group(record: ExperimentRecord) -> str:
        parts = record.experiment_id.split("-")
        marker = max(index for index, part in enumerate(parts) if part == "phase12")
        return "-".join(parts[marker:-1])

    for record in records_a:
        group = record_group(record)
        a_by_group.setdefault(group, []).append(record.verifier_passed)
    for record in records_b:
        group = record_group(record)
        b_by_group.setdefault(group, []).append(record.verifier_passed)
    groups = sorted(set(a_by_group).intersection(b_by_group))
    if not groups:
        raise ValueError("paired bootstrap needs groups present in both methods")
    deltas = [
        sum(a_by_group[group]) / len(a_by_group[group])
        - sum(b_by_group[group]) / len(b_by_group[group])
        for group in groups
    ]
    rng = random.Random(seed)
    draws = [sum(rng.choice(deltas) for _ in groups) / len(groups) for _ in range(samples)]
    draws.sort()
    lower = draws[int(samples * 0.025)]
    upper = draws[min(samples - 1, int(samples * 0.975))]
    delta = sum(deltas) / len(deltas)
    conclusion: Literal["IMPROVED", "INCONCLUSIVE", "REGRESSED"]
    if lower > 0:
        conclusion = "IMPROVED"
    elif upper < 0:
        conclusion = "REGRESSED"
    else:
        conclusion = "INCONCLUSIVE"
    return BootstrapSummary(method_a, method_b, len(groups), delta, lower, upper, conclusion)
