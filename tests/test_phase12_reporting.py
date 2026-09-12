from __future__ import annotations

from pathlib import Path

import pytest

from labs.self_improvement.artifacts import write_manifest, write_records
from labs.self_improvement.data import build_synthetic_manifest
from labs.self_improvement.evaluation import held_out_replay
from labs.self_improvement.replay import deterministic_policy
from labs.self_improvement.reporting import (
    build_summary,
    heldout_bootstrap,
    reward_hacking_evidence,
    summarize_methods,
    write_reports,
)


def test_reporting_keeps_usage_and_failures_in_method_denominator() -> None:
    manifest = build_synthetic_manifest("report-test")
    records = held_out_replay(
        manifest,
        {"baseline": deterministic_policy},
        code_version="report-test",
    )
    summary = summarize_methods(records)
    assert summary["baseline"]["count"] == 24.0
    assert summary["baseline"]["calls"] == 0.0
    assert summary["baseline"]["failed_records"] == 0.0
    assert heldout_bootstrap(records) == ()


def test_reporting_exposes_prespecified_reward_hacking_negative() -> None:
    manifest = build_synthetic_manifest("report-test")
    evidence = reward_hacking_evidence(manifest)
    assert evidence["is_prespecified_negative"] is True
    assert evidence["bad_reward_higher"] is True
    assert evidence["task_verifier_passed"] is False


def test_report_artifacts_are_immutable_and_explicitly_lab_only(tmp_path: Path) -> None:
    manifest = build_synthetic_manifest("report-test")
    write_manifest(tmp_path, manifest)
    records = held_out_replay(
        manifest,
        {"baseline": deterministic_policy, "prompt-candidate": deterministic_policy},
        code_version="report-test",
    )
    write_records(tmp_path, "heldout.jsonl", records)
    summary = build_summary(manifest, records, (), code_version="report-test")
    assert summary["status"] == "DRAFT / LAB_ONLY"
    paths = write_reports(tmp_path, manifest, records, (), code_version="report-test")
    assert all(Path(path).is_file() for path in paths.values())
    report_text = Path(paths["report"]).read_text(encoding="utf-8")
    card_text = Path(paths["adoption_card"]).read_text(encoding="utf-8")
    assert "DRAFT / LAB_ONLY" in report_text
    assert "LAB_ONLY" in card_text
    with pytest.raises(FileExistsError):
        write_reports(tmp_path, manifest, records, (), code_version="report-test")
