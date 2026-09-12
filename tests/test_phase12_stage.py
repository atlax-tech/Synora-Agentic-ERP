from __future__ import annotations

import json
from pathlib import Path

import pytest

from labs.self_improvement.cli import main
from labs.self_improvement.stage import verify_stage


def _seed_lab(root: Path) -> None:
    source = root / "output/phase11/phase11-live-dom-glm-2bb2aa2.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('{"failure_code":"ACTION_REJECTED"}\n', encoding="utf-8")
    assert main(["--root", str(root), "audit-data"]) == 0
    assert main(["--root", str(root), "prepare-data"]) == 0
    assert main(["--root", str(root), "make-candidates"]) == 0


def test_stage_gate_reports_missing_preregistration(tmp_path: Path) -> None:
    _seed_lab(tmp_path)
    result = verify_stage(tmp_path, require_review=False, require_harness=False)
    assert result.status == "INCOMPLETE"
    assert any("experiment plan" in reason for reason in result.reasons)


def test_stage_cli_returns_nonzero_for_incomplete_evidence(tmp_path: Path) -> None:
    _seed_lab(tmp_path)
    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "freeze-experiment",
                "--selected-method",
                "reflection",
            ]
        )
        == 0
    )
    assert main(["--root", str(tmp_path), "verify-stage"]) == 2


def test_stage_result_is_structured_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_lab(tmp_path)
    capsys.readouterr()
    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "verify-stage",
                "--allow-pending-review",
                "--allow-pending-harness",
            ]
        )
        == 2
    )
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert set(payload) == {
        "schema_version",
        "status",
        "reasons",
        "checks",
        "counts",
        "plan_id",
        "dataset_digest",
        "review_status",
        "harness_status",
    }
