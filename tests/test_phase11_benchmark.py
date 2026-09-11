from __future__ import annotations

from typing import cast

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
