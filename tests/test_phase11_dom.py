from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import pytest
import uvicorn

from labs.web_gui.browser import (
    BrowserPolicyError,
    BrowserUnavailable,
    DomSnapshot,
    _validate_action,
    run_dom_task,
    run_security_probe,
)
from labs.web_gui.contracts import ActionProposal, Observation, TaskSpec, TrialBudget
from labs.web_gui.fixtures import create_app
from labs.web_gui.gui import VisualDecision, run_visual_task
from labs.web_gui.security import BrowserSecurityPolicy


@contextmanager
def _server() -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_dom_task_reads_one_order_and_keeps_trace() -> None:
    try:
        with _server() as base_url:
            run = run_dom_task(
                base_url,
                TaskSpec(case_id="p11-dom-001", purchase_order="PUR-ORD-0001", mode="dom"),
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "SUCCEEDED"
    assert run.result.fields == {
        "purchase_order": "PUR-ORD-0001",
        "supplier": "Supplier A",
        "status": "To Receive and Bill",
        "currency": "CNY",
    }
    assert len(run.observations) == 3
    assert len(run.result.actions) == 2
    assert run.result.evidence_refs == tuple(item.observation_id for item in run.observations)


def test_dom_task_reports_missing_order() -> None:
    try:
        with _server() as base_url:
            run = run_dom_task(
                base_url,
                TaskSpec(case_id="p11-dom-002", purchase_order="PUR-ORD-9999", mode="dom"),
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "NOT_FOUND"
    assert run.result.fields == {}


def test_aria_task_uses_accessible_roles_and_reads_one_order() -> None:
    try:
        with _server() as base_url:
            run = run_dom_task(
                base_url,
                TaskSpec(case_id="p11-aria-001", purchase_order="PUR-ORD-0002", mode="aria"),
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "SUCCEEDED"
    assert run.result.fields["currency"] == "USD"
    assert "textbox" in run.observations[0].content
    assert "search-input" not in run.observations[0].content


def test_dom_task_waits_for_observable_async_ready_marker() -> None:
    try:
        with _server() as base_url:
            run = run_dom_task(
                base_url,
                TaskSpec(
                    case_id="p11-async-001",
                    purchase_order="PUR-ORD-0001",
                    mode="dom",
                    scenario="async",
                    budget=TrialBudget(action_timeout_seconds=2.0),
                ),
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "SUCCEEDED"
    assert (
        "Data ready" in run.observations[0].content or "Data ready" in run.observations[1].content
    )


def test_dom_task_stops_when_async_page_never_becomes_ready() -> None:
    try:
        with _server() as base_url:
            run = run_dom_task(
                base_url,
                TaskSpec(
                    case_id="p11-async-002",
                    purchase_order="PUR-ORD-0001",
                    mode="dom",
                    scenario="timeout",
                    budget=TrialBudget(action_timeout_seconds=0.05, wall_time_seconds=1.0),
                ),
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "FAILED"
    assert run.result.stop_reason == "PAGE_NOT_READY"


def test_dom_task_stops_on_permission_denial() -> None:
    try:
        with _server() as base_url:
            run = run_dom_task(
                base_url,
                TaskSpec(
                    case_id="p11-authz-001",
                    purchase_order="PUR-ORD-0001",
                    mode="dom",
                    scenario="permission",
                ),
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "PERMISSION_DENIED"
    assert run.result.fields == {}


def test_dom_task_does_not_treat_expired_session_as_success() -> None:
    try:
        with _server() as base_url:
            run = run_dom_task(
                base_url,
                TaskSpec(
                    case_id="p11-authz-002",
                    purchase_order="PUR-ORD-0001",
                    mode="dom",
                    scenario="auth_expired",
                ),
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "AUTH_REQUIRED"
    assert run.result.fields == {}
    assert "Sign in again" in run.observations[-1].content


def test_changed_page_failure_artifact_is_preserved() -> None:
    artifact = json.loads((Path("output/phase11/phase11-page-change-failure-v1.json")).read_text())
    assert artifact["observed_status"] == "NOT_FOUND"
    assert artifact["preserved"] is True


def test_changed_page_is_recovered_without_v1_regression() -> None:
    try:
        with _server() as base_url:
            run = run_dom_task(
                base_url,
                TaskSpec(
                    case_id="p11-page-change-001",
                    purchase_order="PUR-ORD-0001",
                    mode="dom",
                    scenario="changed",
                ),
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "SUCCEEDED"
    assert "synthetic-procurement-v2" in run.observations[0].content


def test_dom_policy_rejects_stale_or_unknown_targets() -> None:
    proposal = ActionProposal(
        action_type="click",
        observation_id=UUID("00000000-0000-0000-0000-000000000001"),
        target_ref="order:PUR-ORD-0001",
    )
    assert proposal.target_ref == "order:PUR-ORD-0001"
    snapshot = DomSnapshot(
        observation=Observation(
            observation_id=UUID("00000000-0000-0000-0000-000000000001"),
            page_version="synthetic-procurement-v1",
            source="synthetic",
            mode="dom",
        ),
        targets=frozenset({"search-input"}),
    )
    with pytest.raises(BrowserPolicyError, match="not present"):
        _validate_action(proposal, snapshot)
    with pytest.raises(ValueError):
        ActionProposal(
            action_type="click",
            observation_id=UUID("00000000-0000-0000-0000-000000000001"),
            target_ref="order:PUR-ORD-0001",
            script="window.location='https://evil.example'",  # type: ignore[call-arg]
        )


def test_browser_security_policy_allows_only_loopback_read_routes() -> None:
    policy = BrowserSecurityPolicy("http://127.0.0.1:8765")
    assert policy.permits("http://127.0.0.1:8765/?q=PUR-ORD-0001")
    assert policy.permits("http://127.0.0.1:8765/api/purchase-orders/PUR-ORD-0001")
    assert not policy.permits("https://evil.example/collect")
    assert not policy.permits("http://127.0.0.1:8765/api/purchase-orders", "POST")
    assert len(policy.violations) == 2


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("external", "evil.example"),
        ("download", "DOWNLOAD_BLOCKED"),
        ("popup", "evil.example"),
        ("confirm", "DIALOG_DISMISSED"),
    ],
)
def test_browser_security_probe_blocks_side_effect_surfaces(scenario: str, expected: str) -> None:
    try:
        with _server() as base_url:
            violations = run_security_probe(base_url, scenario)
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert any(expected in violation for violation in violations)


def test_browser_security_probe_rejects_write_action() -> None:
    try:
        with _server() as base_url:
            violations = run_security_probe(base_url, "write")
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert violations == ("WRITE_ACTION_REJECTED",)


def test_visual_task_uses_only_screenshots_and_validates_final_fields() -> None:
    seen: list[tuple[type[object], str]] = []
    calls = 0

    def decider(image: bytes, observation: object, spec: TaskSpec) -> VisualDecision:
        nonlocal calls
        calls += 1
        assert isinstance(image, bytes)
        assert not hasattr(observation, "text")
        seen.append((type(image), spec.purchase_order))
        if calls == 1:
            return VisualDecision(
                proposal=ActionProposal(
                    action_type="click",
                    observation_id=observation.observation_id,  # type: ignore[attr-defined]
                    x=700,
                    y=300,
                )
            )
        return VisualDecision(
            proposal=ActionProposal(
                action_type="finish",
                observation_id=observation.observation_id,  # type: ignore[attr-defined]
            ),
            fields={
                "purchase_order": "PUR-ORD-0001",
                "supplier": "Supplier A",
                "status": "To Receive and Bill",
                "currency": "CNY",
            },
        )

    try:
        with _server() as base_url:
            run = run_visual_task(
                base_url,
                TaskSpec(case_id="p11-vision-001", purchase_order="PUR-ORD-0001", mode="vision"),
                decider,
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "SUCCEEDED"
    assert len(run.screenshots) == 2
    assert all(kind is bytes for kind, _ in seen)


def test_visual_action_rejects_out_of_viewport_coordinates() -> None:
    from labs.web_gui.gui import _validate_visual_action

    observation = Observation(
        page_version="screenshot:test",
        source="synthetic",
        mode="vision",
        viewport_width=100,
        viewport_height=100,
    )
    proposal = ActionProposal(
        action_type="click",
        observation_id=observation.observation_id,
        x=100,
        y=50,
    )
    with pytest.raises(BrowserPolicyError, match="outside"):
        _validate_visual_action(proposal, observation)


def test_visual_task_stops_at_model_call_budget() -> None:
    def decider(_image: bytes, observation: object, _spec: TaskSpec) -> VisualDecision:
        return VisualDecision(
            proposal=ActionProposal(
                action_type="wait",
                observation_id=observation.observation_id,  # type: ignore[attr-defined]
            )
        )

    try:
        with _server() as base_url:
            run = run_visual_task(
                base_url,
                TaskSpec(
                    case_id="p11-vision-budget",
                    purchase_order="PUR-ORD-0001",
                    mode="vision",
                    budget=TrialBudget(max_model_calls=1),
                ),
                decider,
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.model_calls == 1
    assert run.result.status == "BUDGET_EXCEEDED"
    assert run.result.stop_reason == "model_call_budget"
