from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
PHASE11 = ROOT / "output" / "phase11"
LIVE_ARTIFACT = PHASE11 / "phase11-benchmark-erp-readonly-live-reconciled-8fd20d8.json"
REPORT = PHASE11 / "phase11-stage-report-draft-c986c27.md"


def test_reconciled_visual_counts_match_immutable_runs() -> None:
    data = json.loads(LIVE_ARTIFACT.read_text(encoding="utf-8"))
    statuses = ("SUCCEEDED", "INCOMPLETE", "BLOCKED", "FAILED", "BUDGET_EXCEEDED")
    runs = data["visual_runs"]

    assert data["reconciled_from"] == "phase11-benchmark-erp-readonly-live-d444e97.json"
    assert data["visual_status_counts"] == {
        status: sum(item["status"] == status for item in runs) for status in statuses
    }


def test_report_binds_page_repair_digest_to_file() -> None:
    path = PHASE11 / "phase11-page-change-repair-v1.json"
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    report = REPORT.read_text(encoding="utf-8")
    match = re.search(r"修复后 .*? SHA-256 `([0-9a-f]{64})`", report)

    assert match is not None
    assert match.group(1) == actual
