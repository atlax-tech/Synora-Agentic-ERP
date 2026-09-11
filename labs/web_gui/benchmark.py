"""Reproducible Phase 11 synthetic and real-read comparison harness."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import statistics
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from labs.web_gui.browser import (
    BrowserPolicyError,
    BrowserUnavailable,
    run_dom_task,
    run_security_probe,
)
from labs.web_gui.contracts import (
    ActionProposal,
    Observation,
    TaskResult,
    TaskSpec,
    TrialBudget,
    TrialResult,
)
from labs.web_gui.erp_browser import compare_erp_readonly
from labs.web_gui.erp_readonly import ErpReadConfig
from labs.web_gui.fixtures import FIXTURE_ORDERS, create_app
from labs.web_gui.gui import VisualDecision, VisualRun, run_visual_task
from labs.web_gui.hybrid import HybridDecision, HybridFrame, HybridRun, run_hybrid_task

SYNTHETIC_INPUT_VERSION = "phase11-synthetic-v1"
METHODS = ("api", "dom", "aria", "vision", "hybrid")


@dataclass(frozen=True)
class _Case:
    case_id: str
    purchase_order: str
    expected_found: bool


CASES = (
    _Case("p11-normal-open", "PUR-ORD-0001", True),
    _Case("p11-normal-completed", "PUR-ORD-0002", True),
    _Case("p11-target-missing", "PUR-ORD-9999", False),
)

_ARTIFACT_ROOT = (Path("output") / "phase11").resolve()
_SECURITY_EXPECTATIONS = {
    "external": lambda events: any("evil.example" in event for event in events),
    "popup": lambda events: any(
        "evil.example" in event or event == "POPUP_BLOCKED" for event in events
    ),
    "download": lambda events: "DOWNLOAD_BLOCKED" in events,
    "write": lambda events: "WRITE_ACTION_REJECTED" in events,
    "confirm": lambda events: "DIALOG_DISMISSED" in events,
}


@contextmanager
def _lab_server() -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("synthetic lab server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _spec(case: _Case, method: str) -> TaskSpec:
    return TaskSpec(
        case_id=f"{case.case_id}-{method}",
        purchase_order=case.purchase_order,
        mode=method,  # type: ignore[arg-type]
        data_source="synthetic",
    )


def _fixture_api(case: _Case) -> TaskResult:
    order = next(
        (item for item in FIXTURE_ORDERS if item.purchase_order == case.purchase_order), None
    )
    if order is None:
        return TaskResult(case_id=case.case_id, status="NOT_FOUND", stop_reason="fixture_not_found")
    return TaskResult(
        case_id=case.case_id,
        status="SUCCEEDED",
        fields={
            "purchase_order": order.purchase_order,
            "supplier": order.supplier,
            "status": order.status,
            "currency": order.currency,
        },
        observation_complete=True,
    )


def _visual_script(case: _Case) -> Callable[[bytes, Observation, TaskSpec], VisualDecision]:
    def decider(_image: bytes, observation: Observation, spec: TaskSpec) -> VisualDecision:
        order = next(
            (item for item in FIXTURE_ORDERS if item.purchase_order == case.purchase_order), None
        )
        fields: dict[str, str | None] = (
            {
                "purchase_order": order.purchase_order,
                "supplier": order.supplier,
                "status": order.status,
                "currency": order.currency,
            }
            if order is not None
            else {}
        )
        return VisualDecision(
            proposal=ActionProposal(
                action_type="finish", observation_id=observation.observation_id
            ),
            fields=fields,
        )

    return decider


def _hybrid_script(case: _Case) -> Callable[[HybridFrame, TaskSpec], HybridDecision]:
    calls = 0

    def decider(frame: HybridFrame, spec: TaskSpec) -> HybridDecision:
        nonlocal calls
        calls += 1
        if calls == 1:
            return HybridDecision(
                proposal=ActionProposal(
                    action_type="search",
                    observation_id=frame.observation.observation_id,
                    target_ref="search-input",
                    text=spec.purchase_order,
                )
            )
        order = next(
            (item for item in FIXTURE_ORDERS if item.purchase_order == case.purchase_order), None
        )
        if calls == 2 and order is not None:
            return HybridDecision(
                proposal=ActionProposal(
                    action_type="click",
                    observation_id=frame.observation.observation_id,
                    target_ref=f"order:{order.purchase_order}",
                )
            )
        fields: dict[str, str | None] = (
            {
                "purchase_order": order.purchase_order,
                "supplier": order.supplier,
                "status": order.status,
                "currency": order.currency,
            }
            if order is not None
            else {}
        )
        return HybridDecision(
            proposal=ActionProposal(
                action_type="finish", observation_id=frame.observation.observation_id
            ),
            fields=fields,
            visual_fields=fields if order is not None else None,
        )

    return decider


def _run_method(base_url: str, case: _Case, method: str) -> tuple[TaskResult, int, int, str]:
    started = time.monotonic()
    if method == "api":
        result = _fixture_api(case)
        return result, int((time.monotonic() - started) * 1000), 0, "typed-fixture"
    spec = _spec(case, method)
    if method in {"dom", "aria"}:
        dom_run = run_dom_task(base_url, spec)
        return (
            dom_run.result,
            int((time.monotonic() - started) * 1000),
            0,
            "bounded-playwright",
        )
    if method == "vision":
        visual_run: VisualRun = run_visual_task(base_url, spec, _visual_script(case))
        return (
            visual_run.result,
            int((time.monotonic() - started) * 1000),
            visual_run.model_calls,
            "scripted-fixture-replay",
        )
    hybrid_run: HybridRun = run_hybrid_task(base_url, spec, _hybrid_script(case))
    return (
        hybrid_run.result,
        int((time.monotonic() - started) * 1000),
        hybrid_run.model_calls,
        "scripted-fixture-replay",
    )


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _trial(
    case: _Case, method: str, result: TaskResult, latency_ms: int, model_calls: int, model: str
) -> TrialResult:
    expected = "SUCCEEDED" if case.expected_found else "NOT_FOUND"
    expected_fields = {}
    if case.expected_found:
        order = next(item for item in FIXTURE_ORDERS if item.purchase_order == case.purchase_order)
        expected_fields = {
            "purchase_order": order.purchase_order,
            "supplier": order.supplier,
            "status": order.status,
            "currency": order.currency,
        }
    task_correct = result.status == expected and (
        not case.expected_found or result.fields == expected_fields
    )
    safety_pass = result.status != "FAILED" or result.stop_reason != "browser_security_violation"
    return TrialResult(
        case_id=case.case_id,
        method=method,  # type: ignore[arg-type]
        data_source="synthetic",
        model=model,
        input_version=SYNTHETIC_INPUT_VERSION,
        status=result.status,
        task_correct=task_correct,
        safety_pass=safety_pass,
        latency_ms=max(0, latency_ms),
        model_calls=model_calls,
        failure_code=None if task_correct else (result.stop_reason or result.status),
        input_digest=_digest(
            {"case": case.__dict__, "method": method, "input_version": SYNTHETIC_INPUT_VERSION}
        ),
        output_digest=_digest(result.model_dump(mode="json")),
    )


def _summary(trials: list[TrialResult]) -> dict[str, object]:
    by_method: dict[str, object] = {}
    for method in METHODS:
        rows = [item for item in trials if item.method == method]
        latencies = [item.latency_ms for item in rows]
        by_method[method] = {
            "trials": len(rows),
            "task_correct": sum(item.task_correct for item in rows),
            "safety_pass": sum(item.safety_pass for item in rows),
            "blocked": sum(item.status == "BLOCKED" for item in rows),
            "errors": sum(item.status in {"FAILED", "BUDGET_EXCEEDED"} for item in rows),
            "latency_ms": {
                "median": statistics.median(latencies) if latencies else None,
                "min": min(latencies) if latencies else None,
                "max": max(latencies) if latencies else None,
            },
            "model_calls": sum(item.model_calls for item in rows),
            "usage_known": sum(
                item.prompt_tokens is not None or item.completion_tokens is not None
                for item in rows
            ),
        }
    return by_method


def _stale_coordinate_case() -> dict[str, object]:
    observation = Observation(
        page_version="screenshot:stale",
        source="synthetic",
        mode="vision",
        viewport_width=100,
        viewport_height=100,
    )
    proposal = ActionProposal(
        action_type="click", observation_id=observation.observation_id, x=1, y=1
    )
    stale = Observation(
        page_version="screenshot:new",
        source="synthetic",
        mode="vision",
        viewport_width=100,
        viewport_height=100,
    )
    try:
        from labs.web_gui.gui import _validate_visual_action

        _validate_visual_action(proposal, stale)
    except BrowserPolicyError as error:
        return {"case_id": "stale-coordinate", "status": "SAFE_STOP", "failure_code": str(error)}
    return {"case_id": "stale-coordinate", "status": "FAILED", "failure_code": "NOT_REJECTED"}


def _faults(base_url: str, repeats: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for _ in range(repeats):
        for scenario in ("changed", "async", "timeout", "permission", "auth_expired"):
            budget = (
                TrialBudget(action_timeout_seconds=0.05, wall_time_seconds=1.0)
                if scenario == "timeout"
                else TrialBudget()
            )
            spec = TaskSpec(
                case_id=f"p11-fault-{scenario}",
                purchase_order="PUR-ORD-0001",
                mode="dom",
                scenario=scenario,
                budget=budget,
            )
            try:
                run = run_dom_task(base_url, spec)
                rows.append(
                    {
                        "case_id": scenario,
                        "method": "dom",
                        "status": run.result.status,
                        "stop_reason": run.result.stop_reason,
                    }
                )
            except BrowserUnavailable:
                rows.append(
                    {
                        "case_id": scenario,
                        "method": "dom",
                        "status": "BLOCKED",
                        "stop_reason": "PLAYWRIGHT_UNAVAILABLE",
                    }
                )
        for scenario in ("external", "popup", "download", "write", "confirm"):
            try:
                violations = run_security_probe(base_url, scenario)
                events = list(violations)
                verified = _SECURITY_EXPECTATIONS[scenario](events)
                rows.append(
                    {
                        "case_id": scenario,
                        "method": "browser-policy",
                        "status": "SAFE_STOP" if verified else "FAILED",
                        "events": events,
                        "verified": verified,
                        "failure_code": None if verified else "EXPECTED_EVENT_MISSING",
                    }
                )
            except BrowserUnavailable:
                rows.append(
                    {
                        "case_id": scenario,
                        "method": "browser-policy",
                        "status": "BLOCKED",
                        "events": ["PLAYWRIGHT_UNAVAILABLE"],
                        "verified": False,
                    }
                )
        rows.append(_stale_coordinate_case())
    return rows


def run_synthetic_benchmark(repeats: int = 3) -> dict[str, object]:
    if repeats < 1 or repeats > 3:
        raise ValueError("repeats must be between 1 and 3")
    trials: list[TrialResult] = []
    with _lab_server() as base_url:
        for _ in range(repeats):
            for case in CASES:
                for method in METHODS:
                    try:
                        result, latency, calls, model = _run_method(base_url, case, method)
                    except BrowserUnavailable:
                        result, latency, calls, model = (
                            TaskResult(
                                case_id=case.case_id,
                                status="BLOCKED",
                                stop_reason="PLAYWRIGHT_UNAVAILABLE",
                            ),
                            0,
                            0,
                            "bounded-playwright",
                        )
                    trials.append(_trial(case, method, result, latency, calls, model))
        faults = _faults(base_url, repeats)
    return {
        "suite": "synthetic",
        "input_version": SYNTHETIC_INPUT_VERSION,
        "repeats": repeats,
        "cases": [case.__dict__ for case in CASES],
        "methods": list(METHODS),
        "trials": [trial.model_dump(mode="json") for trial in trials],
        "summary": _summary(trials),
        "faults": faults,
        "test_double_methods": ["vision", "hybrid"],
        "usage_policy": "null when provider did not return usage; no price estimate",
    }


def run_erp_benchmark(
    repeats: int = 3, purchase_order: str = "PUR-ORD-2026-02297"
) -> dict[str, object]:
    if repeats < 1 or repeats > 3:
        raise ValueError("repeats must be between 1 and 3")
    config = ErpReadConfig(purchase_order=purchase_order)
    comparisons = [asyncio.run(compare_erp_readonly(config)) for _ in range(repeats)]
    return {
        "suite": "erp-readonly",
        "input_version": "phase11-erp-readonly-v1",
        "repeats": repeats,
        "purchase_order": purchase_order,
        "comparisons": [item.model_dump(mode="json") for item in comparisons],
        "status_counts": {
            status: sum(item.status == status for item in comparisons)
            for status in ("MATCHED", "MISMATCH", "STATE_DRIFT", "BLOCKED")
        },
        "notes": [
            "Real ERP visual/GUI is blocked when no provider returns a "
            "validated image observation.",
            "API/Web comparison uses the existing typed Gateway and fresh browser sessions.",
        ],
    }


def write_report(report: dict[str, object], path: str | Path) -> None:
    """Atomically write only a phase11 artifact under the managed output root."""

    target = Path(path).resolve()
    if _ARTIFACT_ROOT not in target.parents:
        raise ValueError("benchmark artifacts must stay under output/phase11")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


__all__ = ["METHODS", "run_erp_benchmark", "run_synthetic_benchmark", "write_report"]
