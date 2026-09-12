from __future__ import annotations

import json
from pathlib import Path

import pytest

from labs.self_improvement.contracts import DatasetCase, ReviewedCase, digest_bytes
from labs.self_improvement.data import (
    audit_historical_failures,
    build_synthetic_manifest,
    validate_grouped_splits,
)

ROOT = Path(__file__).parents[1]


def test_audit_uses_explicit_sources_and_retains_failure_background() -> None:
    cases = audit_historical_failures(ROOT)
    assert cases
    assert any(case.review_status == "BACKGROUND_ONLY" for case in cases)
    assert all(case.input_text for case in cases)
    assert all("Authorization" not in case.input_text for case in cases)


def test_missing_source_is_rejected_without_fabricating_input(tmp_path: Path) -> None:
    cases = audit_historical_failures(tmp_path, ("missing.json",))
    assert cases[0].review_status == "REJECTED"
    assert cases[0].failure_code == "SOURCE_MISSING"


def test_secret_source_is_rejected_before_export(tmp_path: Path) -> None:
    path = tmp_path / "secret.json"
    path.write_text(json.dumps({"api_key": "canary"}), encoding="utf-8")
    cases = audit_historical_failures(tmp_path, ("secret.json",))
    assert cases[0].review_status == "REJECTED"
    assert cases[0].failure_code == "SECRET_PATTERN"


def test_synthetic_manifest_has_grouped_72_24_24_split() -> None:
    manifest = build_synthetic_manifest("test-code")
    assert manifest.split_counts == {"train": 72, "dev": 24, "test": 24}
    assert manifest.group_counts == {"train": 36, "dev": 12, "test": 12}
    validate_grouped_splits(manifest.cases)
    assert len({case.group_id for case in manifest.cases}) == 60


def test_cross_split_group_is_rejected() -> None:
    cases = [
        DatasetCase(
            case_id="phase12-case-one",
            group_id="phase12-group-one",
            split="train",
            kind="COMPLETE_READ",
            source_kind="SYNTHETIC",
            input_text="x",
            expected_action="FINISH",
            expected_status="SUCCEEDED",
        ),
        DatasetCase(
            case_id="phase12-case-two",
            group_id="phase12-group-one",
            split="test",
            kind="COMPLETE_READ",
            source_kind="SYNTHETIC",
            input_text="y",
            expected_action="FINISH",
            expected_status="SUCCEEDED",
        ),
    ]
    with pytest.raises(ValueError, match="cross splits"):
        validate_grouped_splits(cases)


def test_reviewed_case_rejects_unknown_accepted_status() -> None:
    with pytest.raises(ValueError):
        ReviewedCase(
            case_id="phase12-accepted-unknown",
            source_path="x.json",
            source_sha256=digest_bytes(b"x"),
            source_kind="SYNTHETIC",
            group_id="phase12-group-x",
            kind="TOOL_UNKNOWN",
            review_status="ACCEPTED",
            input_text="x",
            expected_action="ASK_INPUT",
            expected_status="UNKNOWN",
            review_reason="bad",
        )
