from __future__ import annotations

import pytest

from labs.web_gui.benchmark import METHODS, run_synthetic_benchmark


@pytest.fixture(scope="module")
def synthetic_report() -> dict[str, object]:
    return run_synthetic_benchmark(repeats=1)


def test_synthetic_benchmark_freezes_all_methods_and_fault_cases(
    synthetic_report: dict[str, object],
) -> None:
    report = synthetic_report

    assert report["suite"] == "synthetic"
    assert report["methods"] == list(METHODS)
    assert len(report["trials"]) == 15
    summary = report["summary"]
    assert isinstance(summary, dict)
    assert set(summary) == set(METHODS)
    assert all(item["trials"] == 3 for item in summary.values())
    fault_ids = {item["case_id"] for item in report["faults"]}
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


def test_synthetic_trials_include_frozen_model_and_input_metadata(
    synthetic_report: dict[str, object],
) -> None:
    report = synthetic_report
    trial = report["trials"][0]

    assert trial["model"]
    assert trial["input_version"] == "phase11-synthetic-v1"
    assert len(trial["input_digest"]) == 64
    assert len(trial["output_digest"]) == 64
