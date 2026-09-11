from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar
from uuid import UUID

import pytest
import uvicorn

from labs.web_gui.browser import (
    BrowserPolicyError,
    BrowserUnavailable,
    DomSnapshot,
    _apply_action,
    _validate_action,
    run_dom_task,
    run_security_probe,
)
from labs.web_gui.contracts import ActionProposal, Observation, TaskSpec, TrialBudget
from labs.web_gui.fixtures import create_app
from labs.web_gui.gui import VisualDecision, _visual_observation, run_visual_task
from labs.web_gui.hybrid import HybridDecision, run_hybrid_task
from labs.web_gui.recovery import RecoveryFailure
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
    aria_observation = json.loads(run.observations[0].content)
    assert "textbox" in aria_observation["aria"]
    assert "search-input" in aria_observation["targets"]


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


def test_dom_apply_action_records_policy_rejection() -> None:
    snapshot = DomSnapshot(
        observation=Observation(
            page_version="synthetic-v1",
            source="synthetic",
            mode="dom",
        ),
        targets=frozenset(),
    )
    proposal = ActionProposal(
        action_type="click",
        observation_id=snapshot.observation.observation_id,
        target_ref="order:PUR-ORD-0001",
    )
    receipt = _apply_action(
        None,
        proposal,
        snapshot,
        TaskSpec(case_id="p11-dom-reject", purchase_order="PUR-ORD-0001"),
    )
    assert receipt.result == "REJECTED"
    assert receipt.error_code == "ACTION_REJECTED"


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
    assert run.result.actions[0].after_observation_id is not None


def test_visual_task_waits_for_ready_before_model_observation() -> None:
    calls = 0

    def decider(_image: bytes, observation: object, _spec: TaskSpec) -> VisualDecision:
        nonlocal calls
        calls += 1
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
                TaskSpec(
                    case_id="p11-vision-async-ready",
                    purchase_order="PUR-ORD-0001",
                    mode="vision",
                    scenario="async",
                ),
                decider,
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "SUCCEEDED"
    assert calls == 2


def test_visual_task_never_sends_terminal_initial_page_to_model() -> None:
    called = False

    def decider(_image: bytes, _observation: object, _spec: TaskSpec) -> VisualDecision:
        nonlocal called
        called = True
        raise AssertionError("terminal page must not reach the visual model")

    try:
        with _server() as base_url:
            run = run_visual_task(
                base_url,
                TaskSpec(
                    case_id="p11-vision-permission",
                    purchase_order="PUR-ORD-0001",
                    mode="vision",
                    scenario="permission",
                ),
                decider,
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "PERMISSION_DENIED"
    assert run.result.stop_reason == "PERMISSION_DENIED"
    assert called is False
    assert run.observations == ()
    assert run.screenshots == ()


def test_visual_task_stops_before_capturing_expired_session_page() -> None:
    calls = 0

    def decider(_image: bytes, observation: object, _spec: TaskSpec) -> VisualDecision:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("expired session page must not reach the visual model")
        return VisualDecision(
            proposal=ActionProposal(
                action_type="click",
                observation_id=observation.observation_id,  # type: ignore[attr-defined]
                x=700,
                y=340,
            )
        )

    try:
        with _server() as base_url:
            run = run_visual_task(
                base_url,
                TaskSpec(
                    case_id="p11-vision-auth-expired",
                    purchase_order="PUR-ORD-0001",
                    mode="vision",
                    scenario="auth_expired",
                ),
                decider,
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "AUTH_REQUIRED"
    assert run.result.stop_reason == "AUTH_REQUIRED"
    assert calls == 1
    assert len(run.screenshots) >= 1
    assert all("Sign in again" not in item.content for item in run.observations)


def test_visual_task_stops_when_page_never_becomes_ready() -> None:
    called = False

    def decider(_image: bytes, _observation: object, _spec: TaskSpec) -> VisualDecision:
        nonlocal called
        called = True
        raise AssertionError("unready page must not reach the visual model")

    try:
        with _server() as base_url:
            run = run_visual_task(
                base_url,
                TaskSpec(
                    case_id="p11-vision-page-timeout",
                    purchase_order="PUR-ORD-0001",
                    mode="vision",
                    scenario="timeout",
                    budget=TrialBudget(action_timeout_seconds=0.05, wall_time_seconds=1.0),
                ),
                decider,
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "FAILED"
    assert run.result.stop_reason == "PAGE_NOT_READY"
    assert called is False


def test_generic_visual_runner_rejects_real_source_before_decider() -> None:
    called = False

    def decider(_image: bytes, _observation: object, _spec: TaskSpec) -> VisualDecision:
        nonlocal called
        called = True
        raise AssertionError("real ERP data must use the redacted runner")

    with pytest.raises(BrowserPolicyError, match="synthetic data only"):
        run_visual_task(
            "http://127.0.0.1:8765",
            TaskSpec(
                case_id="p11-vision-real-source",
                purchase_order="PUR-ORD-2026-02297",
                mode="vision",
                data_source="erp_readonly",
            ),
            decider,
        )
    assert called is False


@pytest.mark.parametrize("mode", ["dom", "aria"])
def test_generic_dom_runners_reject_real_source_before_browser(mode: str) -> None:
    with pytest.raises(BrowserPolicyError, match="synthetic data only"):
        run_dom_task(
            "http://127.0.0.1:8765",
            TaskSpec(
                case_id=f"p11-{mode}-real-source",
                purchase_order="PUR-ORD-2026-02297",
                mode=mode,  # type: ignore[arg-type]
                data_source="erp_readonly",
            ),
        )


def test_visual_task_rejects_nonempty_wrong_trusted_fields() -> None:
    def decider(_image: bytes, observation: object, _spec: TaskSpec) -> VisualDecision:
        return VisualDecision(
            proposal=ActionProposal(
                action_type="finish",
                observation_id=observation.observation_id,  # type: ignore[attr-defined]
            ),
            fields={
                "purchase_order": "PUR-ORD-0001",
                "supplier": "Wrong supplier",
                "status": "To Receive and Bill",
                "currency": "CNY",
            },
        )

    try:
        with _server() as base_url:
            run = run_visual_task(
                base_url,
                TaskSpec(
                    case_id="p11-vision-wrong-fields", purchase_order="PUR-ORD-0001", mode="vision"
                ),
                decider,
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "INCOMPLETE"
    assert run.result.fields == {}
    assert run.result.stop_reason == "visual_fields_mismatch"


def test_generic_hybrid_runner_rejects_real_source_before_decider() -> None:
    called = False

    def decider(_frame: object, _spec: TaskSpec) -> HybridDecision:
        nonlocal called
        called = True
        raise AssertionError("real ERP data must use the redacted runner")

    with pytest.raises(BrowserPolicyError, match="synthetic data only"):
        run_hybrid_task(
            "http://127.0.0.1:8765",
            TaskSpec(
                case_id="p11-hybrid-real-source",
                purchase_order="PUR-ORD-2026-02297",
                mode="hybrid",
                data_source="erp_readonly",
            ),
            decider,
        )
    assert called is False


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


def test_visual_observation_timeout_is_a_bounded_failure() -> None:
    class _BlockedPage:
        viewport_size: ClassVar[dict[str, int]] = {"width": 100, "height": 100}

        def screenshot(self, **_kwargs: object) -> bytes:
            raise TimeoutError("blocked screenshot")

    with pytest.raises(RecoveryFailure, match="OBSERVATION_TIMEOUT"):
        _visual_observation(_BlockedPage(), "synthetic", timeout_ms=5)


def test_visual_runner_turns_initial_observation_timeout_into_budget_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import labs.web_gui.gui as gui_module

    def blocked(*_args: object, **_kwargs: object) -> tuple[Observation, bytes]:
        raise RecoveryFailure("OBSERVATION_TIMEOUT")

    monkeypatch.setattr(gui_module, "_visual_observation", blocked)
    try:
        with _server() as base_url:
            run = run_visual_task(
                base_url,
                TaskSpec(
                    case_id="p11-vision-observation-budget",
                    purchase_order="PUR-ORD-0001",
                    mode="vision",
                ),
                lambda _image, _observation, _spec: pytest.fail("decider must not run"),
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "BUDGET_EXCEEDED"
    assert run.result.stop_reason == "OBSERVATION_TIMEOUT"


def test_visual_task_stops_repeated_waits_as_no_progress() -> None:
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
                    case_id="p11-vision-no-progress", purchase_order="PUR-ORD-0001", mode="vision"
                ),
                decider,
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.model_calls == 2
    assert run.result.status == "FAILED"
    assert run.result.stop_reason == "NO_PROGRESS"


def test_visual_task_enforces_model_deadline_and_output_budget() -> None:
    def slow_decider(_image: bytes, observation: object, _spec: TaskSpec) -> VisualDecision:
        time.sleep(0.12)
        return VisualDecision(
            proposal=ActionProposal(
                action_type="finish",
                observation_id=observation.observation_id,  # type: ignore[attr-defined]
            )
        )

    try:
        with _server() as base_url:
            timed = run_visual_task(
                base_url,
                TaskSpec(
                    case_id="p11-vision-model-timeout",
                    purchase_order="PUR-ORD-0001",
                    mode="vision",
                    budget=TrialBudget(action_timeout_seconds=0.1, model_timeout_seconds=0.1),
                ),
                slow_decider,
            )
            limited = run_visual_task(
                base_url,
                TaskSpec(
                    case_id="p11-vision-output-budget",
                    purchase_order="PUR-ORD-0001",
                    mode="vision",
                    budget=TrialBudget(max_output_tokens=1),
                ),
                lambda _image, observation, _spec: VisualDecision(
                    proposal=ActionProposal(
                        action_type="finish", observation_id=observation.observation_id
                    ),
                    fields={
                        "purchase_order": "PUR-ORD-0001",
                        "supplier": "Supplier A",
                        "status": "To Receive and Bill",
                        "currency": "CNY",
                    },
                ),
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert timed.result.status == "BUDGET_EXCEEDED"
    assert timed.result.stop_reason == "model_timeout"
    assert limited.result.status == "BUDGET_EXCEEDED"
    assert limited.result.stop_reason == "model_output_budget"


def test_visual_model_timeout_is_separate_from_page_action_timeout() -> None:
    def slow_decider(_image: bytes, observation: object, _spec: TaskSpec) -> VisualDecision:
        time.sleep(0.12)
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
                TaskSpec(
                    case_id="p11-vision-model-budget-separated",
                    purchase_order="PUR-ORD-0001",
                    mode="vision",
                    budget=TrialBudget(action_timeout_seconds=1.0, model_timeout_seconds=0.2),
                ),
                slow_decider,
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "SUCCEEDED"


def test_visual_task_records_stale_action_as_structured_rejection() -> None:
    def decider(_image: bytes, _observation: object, _spec: TaskSpec) -> VisualDecision:
        return VisualDecision(
            proposal=ActionProposal(
                action_type="click",
                observation_id=UUID("00000000-0000-0000-0000-000000000001"),
                x=1,
                y=1,
            )
        )

    try:
        with _server() as base_url:
            run = run_visual_task(
                base_url,
                TaskSpec(
                    case_id="p11-vision-stale-action", purchase_order="PUR-ORD-0001", mode="vision"
                ),
                decider,
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "FAILED"
    assert run.result.stop_reason == "ACTION_REJECTED"
    assert run.result.actions[0].result == "REJECTED"


def test_visual_task_rechecks_page_version_before_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import labs.web_gui.gui as gui_module

    original = gui_module._visual_observation
    calls = 0

    def changed(
        page: object, source: str, *, timeout_ms: int | None = None
    ) -> tuple[Observation, bytes]:
        nonlocal calls
        calls += 1
        observation, screenshot = original(page, source, timeout_ms=timeout_ms)
        if calls == 2:
            observation = observation.model_copy(
                update={"page_version": observation.page_version + ":changed"}
            )
        return observation, screenshot

    monkeypatch.setattr(gui_module, "_visual_observation", changed)

    def decider(_image: bytes, observation: object, _spec: TaskSpec) -> VisualDecision:
        return VisualDecision(
            proposal=ActionProposal(
                action_type="click",
                observation_id=observation.observation_id,  # type: ignore[attr-defined]
                x=700,
                y=300,
            )
        )

    try:
        with _server() as base_url:
            run = run_visual_task(
                base_url,
                TaskSpec(
                    case_id="p11-vision-stale-page", purchase_order="PUR-ORD-0001", mode="vision"
                ),
                decider,
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "FAILED"
    assert run.result.stop_reason == "STALE_OBSERVATION"
    assert run.result.actions[0].error_code == "STALE_OBSERVATION"
