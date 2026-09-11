from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import pytest
import uvicorn

from labs.web_gui.browser import (
    BrowserPolicyError,
    BrowserUnavailable,
    DomSnapshot,
    _validate_action,
    run_dom_task,
)
from labs.web_gui.contracts import ActionProposal, Observation, TaskSpec
from labs.web_gui.fixtures import create_app


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
