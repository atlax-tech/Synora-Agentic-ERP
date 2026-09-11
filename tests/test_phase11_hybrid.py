from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import uvicorn

from labs.web_gui.browser import BrowserPolicyError, BrowserUnavailable
from labs.web_gui.contracts import ActionProposal, Observation, TaskSpec, TrialBudget
from labs.web_gui.fixtures import create_app
from labs.web_gui.hybrid import (
    HybridDecision,
    HybridFrame,
    _validate_hybrid_action,
    run_hybrid_task,
)


@contextmanager
def _server() -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
    )
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


def test_hybrid_task_keeps_dom_aria_and_screenshot_in_one_frame() -> None:
    calls = 0

    def decider(frame: object, spec: TaskSpec) -> HybridDecision:
        nonlocal calls
        calls += 1
        assert frame.observation.mode == "hybrid"  # type: ignore[attr-defined]
        assert frame.dom_text  # type: ignore[attr-defined]
        assert frame.aria_text  # type: ignore[attr-defined]
        assert frame.screenshot.startswith(b"\x89PNG")  # type: ignore[attr-defined]
        if calls == 1:
            return HybridDecision(
                proposal=ActionProposal(
                    action_type="search",
                    observation_id=frame.observation.observation_id,  # type: ignore[attr-defined]
                    target_ref="search-input",
                    text=spec.purchase_order,
                )
            )
        if calls == 2:
            return HybridDecision(
                proposal=ActionProposal(
                    action_type="click",
                    observation_id=frame.observation.observation_id,  # type: ignore[attr-defined]
                    target_ref="order:PUR-ORD-0001",
                )
            )
        return HybridDecision(
            proposal=ActionProposal(
                action_type="finish",
                observation_id=frame.observation.observation_id,  # type: ignore[attr-defined]
            ),
            fields={
                "purchase_order": "PUR-ORD-0001",
                "supplier": "Supplier A",
                "status": "To Receive and Bill",
                "currency": "CNY",
            },
            visual_fields={
                "purchase_order": "PUR-ORD-0001",
                "supplier": "Supplier A",
                "status": "To Receive and Bill",
                "currency": "CNY",
            },
        )

    try:
        with _server() as base_url:
            run = run_hybrid_task(
                base_url,
                TaskSpec(case_id="p11-hybrid-001", purchase_order="PUR-ORD-0001", mode="hybrid"),
                decider,
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "SUCCEEDED"
    assert len(run.frames) == 3
    assert all(frame.observation.page_version.startswith("hybrid:") for frame in run.frames)


def test_hybrid_conflict_stops_without_silent_fallback() -> None:
    def decider(frame: object, _spec: TaskSpec) -> HybridDecision:
        return HybridDecision(
            proposal=ActionProposal(
                action_type="finish",
                observation_id=frame.observation.observation_id,  # type: ignore[attr-defined]
            ),
            fields={"purchase_order": "PUR-ORD-0001"},
            visual_fields={"purchase_order": "PUR-ORD-0002"},
        )

    try:
        with _server() as base_url:
            run = run_hybrid_task(
                base_url,
                TaskSpec(case_id="p11-hybrid-002", purchase_order="PUR-ORD-0001", mode="hybrid"),
                decider,
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.result.status == "OBSERVATION_CONFLICT"
    assert run.result.fields == {}
    assert run.result.stop_reason == "dom_and_visual_answers_conflict"


def test_hybrid_task_stops_at_model_call_budget() -> None:
    def decider(frame: object, _spec: TaskSpec) -> HybridDecision:
        return HybridDecision(
            proposal=ActionProposal(
                action_type="wait",
                observation_id=frame.observation.observation_id,  # type: ignore[attr-defined]
            )
        )

    try:
        with _server() as base_url:
            run = run_hybrid_task(
                base_url,
                TaskSpec(
                    case_id="p11-hybrid-budget",
                    purchase_order="PUR-ORD-0001",
                    mode="hybrid",
                    budget=TrialBudget(max_model_calls=1),
                ),
                decider,
            )
    except BrowserUnavailable:
        pytest.skip("web-gui-lab is not installed")

    assert run.model_calls == 1
    assert run.result.status == "BUDGET_EXCEEDED"
    assert run.result.stop_reason == "model_call_budget"


def test_hybrid_rejects_coordinate_or_unknown_actions() -> None:
    frame = HybridFrame(
        observation=Observation(
            page_version="hybrid:test",
            source="synthetic",
            mode="hybrid",
            viewport_width=100,
            viewport_height=100,
        ),
        dom_text="",
        aria_text="",
        screenshot=b"",
        targets=frozenset({"search-input"}),
    )
    proposal = ActionProposal(
        action_type="click",
        observation_id=frame.observation.observation_id,
        target_ref="search-input",
        x=1,
        y=1,
    )
    with pytest.raises(BrowserPolicyError, match="coordinates"):
        _validate_hybrid_action(HybridDecision(proposal=proposal), frame)
