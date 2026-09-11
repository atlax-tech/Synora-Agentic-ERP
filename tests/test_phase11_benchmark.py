from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from labs.web_gui.benchmark import (
    _VISUAL_STATUSES,
    CASES,
    METHODS,
    _run_method,
    run_synthetic_benchmark,
    write_report,
)


@pytest.fixture(scope="module")
def synthetic_report() -> dict[str, object]:
    return run_synthetic_benchmark(repeats=1)


def test_synthetic_benchmark_freezes_all_methods_and_fault_cases(
    synthetic_report: dict[str, object],
) -> None:
    report = synthetic_report

    assert report["suite"] == "synthetic"
    assert report["methods"] == list(METHODS)
    trials = cast(list[dict[str, object]], report["trials"])
    assert len(trials) == 15
    summary = cast(dict[str, dict[str, object]], report["summary"])
    assert set(summary) == set(METHODS)
    assert all(item["trials"] == 3 for item in summary.values())
    faults = cast(list[dict[str, object]], report["faults"])
    fault_ids = {item["case_id"] for item in faults}
    assert {
        "changed",
        "async",
        "timeout",
        "permission",
        "auth_expired",
        "external",
        "popup",
        "download",
        "write",
        "confirm",
        "stale-coordinate",
    } <= fault_ids
    page_faults = [
        item
        for item in faults
        if item["case_id"] in {"changed", "async", "timeout", "permission", "auth_expired"}
    ]
    assert len(page_faults) == 5 * 4
    assert {
        (item["case_id"], item["method"])
        for item in page_faults
    } == {
        (scenario, method)
        for scenario in {"changed", "async", "timeout", "permission", "auth_expired"}
        for method in {"dom", "aria", "vision", "hybrid"}
    }
    assert all(item["applicable"] is True for item in page_faults)
    security_faults = [
        item
        for item in faults
        if item["case_id"] in {"external", "popup", "download", "write", "confirm"}
    ]
    assert security_faults
    assert all(
        item["status"] == "SAFE_STOP" and item["verified"] is True for item in security_faults
    )


def test_synthetic_trials_include_frozen_model_and_input_metadata(
    synthetic_report: dict[str, object],
) -> None:
    report = synthetic_report
    trial = cast(list[dict[str, object]], report["trials"])[0]

    assert trial["model"]
    assert trial["input_version"] == "phase11-synthetic-v1"
    assert isinstance(trial["input_digest"], str)
    assert isinstance(trial["output_digest"], str)
    assert len(trial["input_digest"]) == 64
    assert len(trial["output_digest"]) == 64


def test_benchmark_declares_engine_and_does_not_label_deterministic_vision_live(
    synthetic_report: dict[str, object],
) -> None:
    assert synthetic_report["engine"] == "deterministic"
    assert synthetic_report["text_role"] is None
    assert synthetic_report["vision_role"] is None
    assert synthetic_report["test_double_methods"] == ["vision", "hybrid"]


def test_api_engine_selection_keeps_live_metadata_shape() -> None:
    with pytest.raises(ValueError, match="engine"):
        _run_method("http://127.0.0.1:1", CASES[0], "api", engine="invalid")

    result, latency, calls, model, prompt_tokens, completion_tokens = _run_method(
        "http://127.0.0.1:1", CASES[0], "api", engine="live"
    )
    assert result.status == "SUCCEEDED"
    assert latency >= 0
    assert calls == 0
    assert model == "typed-fixture"
    assert prompt_tokens is None
    assert completion_tokens is None


def test_benchmark_artifacts_are_allowlisted_and_atomic(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="output/phase11"):
        write_report({}, tmp_path / "outside.json")

    target = Path("output/phase11/.phase11-test-atomic.json")
    try:
        write_report({"status": "ok"}, target)
        assert json.loads(target.read_text()) == {"status": "ok"}
    finally:
        target.unlink(missing_ok=True)


def test_visual_status_inventory_includes_incomplete_runs() -> None:
    statuses = ["SUCCEEDED", "INCOMPLETE", "INCOMPLETE", "BLOCKED"]
    counts = {status: sum(item == status for item in statuses) for status in _VISUAL_STATUSES}

    assert counts == {
        "SUCCEEDED": 1,
        "INCOMPLETE": 2,
        "BLOCKED": 1,
        "FAILED": 0,
        "BUDGET_EXCEEDED": 0,
    }
