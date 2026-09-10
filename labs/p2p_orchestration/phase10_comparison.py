"""Deterministic LAB_ONLY comparison for durable P2P orchestration.

The event stream is deliberately only a wake-up hint.  Every strategy reads
the same in-memory ERP fact snapshot; no event field is treated as approval,
identity, quantity or a writer command.  The test double models duplicates,
out-of-order delivery, a delayed timer wake-up and a process restart without
connecting to Frappe or storing credentials.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

STRATEGIES = ("single_agent", "fixed_workflow", "multi_agent")
ACTIONS = (
    "SUBMIT_PO",
    "CREATE_PR_DRAFT",
    "SUBMIT_PR",
    "CREATE_PI_DRAFT",
    "SUBMIT_PI",
    "CREATE_PAYMENT_ENTRY_DRAFT",
    "SUBMIT_PAYMENT_ENTRY",
)
DEPENDENCIES = {
    action: ACTIONS[index - 1] if index else None for index, action in enumerate(ACTIONS)
}
MODEL_ID = "recorded-p2p-orchestration-v1"


@dataclass(frozen=True)
class P2PEvent:
    event_id: str
    action: str
    kind: Literal["ERP_WAKE", "TIMER_WAKE", "RESTART"]
    delivery_order: int
    authorization_hint: str = "spoofed-event-data"


@dataclass(frozen=True)
class P2PStrategyResult:
    strategy: str
    status: str
    quality: float
    safety_violations: int
    latency_ms: int
    token_count: int
    recovery_rate: float
    operational_complexity: int
    completed_actions: tuple[str, ...]
    ignored_duplicate_events: int
    restart_recovered: bool
    trace_complete: bool
    note: str


@dataclass(frozen=True)
class P2PExperimentReport:
    schema_version: str
    suite: str
    status: str
    dataset_id: str
    dataset_digest: str
    model_id: str
    initial_erp_facts: dict[str, object]
    event_matrix: tuple[dict[str, object], ...]
    results: tuple[P2PStrategyResult, ...]
    adoption: str
    artifact_policy: str


INITIAL_ERP_FACTS: dict[str, object] = {
    "company": "LAB-COMPANY",
    "supplier": "LAB-SUPPLIER",
    "currency": "CNY",
    "po_qty": "2",
    "received_qty": "0",
    "billed_qty": "0",
    "paid_amount": "0.00",
    "authorization_source": "server-side-plan-step",
}


EVENT_MATRIX: tuple[P2PEvent, ...] = (
    P2PEvent("e-03", "SUBMIT_PR", "ERP_WAKE", 1),
    P2PEvent("e-01", "SUBMIT_PO", "ERP_WAKE", 2),
    P2PEvent("e-01-dup", "SUBMIT_PO", "ERP_WAKE", 3),
    P2PEvent("e-02", "CREATE_PR_DRAFT", "ERP_WAKE", 4),
    P2PEvent("e-04", "CREATE_PI_DRAFT", "TIMER_WAKE", 5),
    P2PEvent("e-restart", "CREATE_PI_DRAFT", "RESTART", 6),
    P2PEvent("e-05", "SUBMIT_PI", "ERP_WAKE", 7),
    P2PEvent("e-06", "CREATE_PAYMENT_ENTRY_DRAFT", "TIMER_WAKE", 8),
    P2PEvent("e-07", "SUBMIT_PAYMENT_ENTRY", "ERP_WAKE", 9),
    P2PEvent("e-07-dup", "SUBMIT_PAYMENT_ENTRY", "ERP_WAKE", 10),
)


def _dataset_digest() -> str:
    body = json.dumps(
        {
            "facts": INITIAL_ERP_FACTS,
            "actions": ACTIONS,
            "events": [asdict(e) for e in EVENT_MATRIX],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _event_is_duplicate(event: P2PEvent, seen_ids: set[str]) -> bool:
    return event.action in seen_ids


def _drain_ready(completed: set[str], pending: set[str]) -> int:
    writes = 0
    changed = True
    while changed:
        changed = False
        for action in ACTIONS:
            if action not in pending:
                continue
            dependency = DEPENDENCIES[action]
            if dependency is not None and dependency not in completed:
                continue
            pending.remove(action)
            completed.add(action)
            writes += 1
            changed = True
    return writes


def _run_single_agent() -> P2PStrategyResult:
    completed: list[str] = []
    seen_events: set[str] = set()
    safety_violations = 0
    duplicates = 0
    for event in EVENT_MATRIX:
        if event.kind == "RESTART":
            # The deliberately non-durable baseline loses its action ledger.
            seen_events = set()
        if event.action in completed:
            duplicates += 1
            safety_violations += 1
        dependency = DEPENDENCIES[event.action]
        if dependency is not None and dependency not in completed:
            # It trusts the event ordering and performs a downstream write.
            safety_violations += 1
        completed.append(event.action)
        seen_events.add(event.action)
    unique_completed = tuple(dict.fromkeys(completed))
    return P2PStrategyResult(
        strategy="single_agent",
        status="FAILED" if safety_violations else "SUCCEEDED",
        quality=len(unique_completed) / len(ACTIONS),
        safety_violations=safety_violations,
        latency_ms=10 * len(EVENT_MATRIX),
        token_count=320,
        recovery_rate=0.0,
        operational_complexity=1,
        completed_actions=unique_completed,
        ignored_duplicate_events=duplicates,
        restart_recovered=False,
        trace_complete=True,
        note="baseline trusts wake-up order and loses in-memory dedupe on restart",
    )


def _run_durable(strategy: str) -> P2PStrategyResult:
    completed: set[str] = set()
    pending: set[str] = set()
    seen_events: set[str] = set()
    duplicates = 0
    writes = 0
    restart_recovered = False
    for event in EVENT_MATRIX:
        if event.kind == "RESTART":
            # The durable PlanStep ledger survives; restart only causes a
            # read-only re-evaluation of the pending graph.
            restart_recovered = True
            writes += _drain_ready(completed, pending)
            continue
        if _event_is_duplicate(event, seen_events):
            duplicates += 1
            continue
        seen_events.add(event.action)
        # authorization_hint is intentionally ignored; current policy and
        # Receipt facts would be checked by the real Frappe service.
        pending.add(event.action)
        writes += _drain_ready(completed, pending)
    writes += _drain_ready(completed, pending)
    safety_violations = 0 if not pending and len(completed) == len(ACTIONS) else 1
    overhead = 5 if strategy == "fixed_workflow" else 12
    return P2PStrategyResult(
        strategy=strategy,
        status="SUCCEEDED" if safety_violations == 0 else "FAILED",
        quality=len(completed) / len(ACTIONS),
        safety_violations=safety_violations,
        latency_ms=overhead * len(EVENT_MATRIX) + writes * 3,
        token_count=0 if strategy == "fixed_workflow" else 640,
        recovery_rate=1.0 if restart_recovered and safety_violations == 0 else 0.0,
        operational_complexity=2 if strategy == "fixed_workflow" else 4,
        completed_actions=tuple(action for action in ACTIONS if action in completed),
        ignored_duplicate_events=duplicates,
        restart_recovered=restart_recovered,
        trace_complete=True,
        note=(
            "durable PlanStep ledger; events only wake read/recheck"
            if strategy == "fixed_workflow"
            else "planner/reviewer adds coordination overhead without new ERP authority"
        ),
    )


def run_phase10_comparison() -> P2PExperimentReport:
    """Run all three strategies against one fixed, replayable event matrix."""

    results = (
        _run_single_agent(),
        _run_durable("fixed_workflow"),
        _run_durable("multi_agent"),
    )
    adopted = next(
        (result for result in results if result.strategy == "fixed_workflow"), results[0]
    )
    if adopted.safety_violations or adopted.recovery_rate < 1.0:
        adoption = "KEEP_LAB_ONLY_BLOCKED"
    elif any(
        result.quality > adopted.quality and result.safety_violations == 0 for result in results
    ):
        adoption = "KEEP_LAB_ONLY_NO_DECISION"
    else:
        adoption = "KEEP_FIXED_WORKFLOW_BUSINESS_BASELINE"
    return P2PExperimentReport(
        schema_version="1",
        suite="P10.7-p2p-orchestration-recovery",
        status="PASS" if all(result.status == "SUCCEEDED" for result in results[1:]) else "BLOCKED",
        dataset_id="phase10-p2p-events-v1",
        dataset_digest=_dataset_digest(),
        model_id=MODEL_ID,
        initial_erp_facts=dict(INITIAL_ERP_FACTS),
        event_matrix=tuple(asdict(event) for event in EVENT_MATRIX),
        results=results,
        adoption=adoption,
        artifact_policy=(
            "recorded test double only; no ERP write, credential, or event authorization"
        ),
    )


def report_as_json(report: P2PExperimentReport) -> dict[str, object]:
    return asdict(report)


__all__ = [
    "EVENT_MATRIX",
    "INITIAL_ERP_FACTS",
    "P2PExperimentReport",
    "P2PStrategyResult",
    "report_as_json",
    "run_phase10_comparison",
]
