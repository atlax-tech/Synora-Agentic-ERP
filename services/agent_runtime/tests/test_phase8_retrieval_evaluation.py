"""Fixed, network-free Phase 8 retrieval baseline evidence."""

import json

from agent_runtime.evaluation.phase8_retrieval import (
    load_phase8_retrieval_dataset,
    run_phase8_retrieval_suite,
)


def test_phase8_retrieval_dataset_has_required_positive_and_negative_cases() -> None:
    dataset = load_phase8_retrieval_dataset()

    assert len(dataset.sources) >= 7
    assert len(dataset.cases) >= 7
    assert {case.case_id for case in dataset.cases} >= {
        "normal-english",
        "cjk-sop",
        "wrong-permission",
        "revision-match",
        "erp-version-match",
        "unrelated",
        "retrieval-injection",
    }


def test_phase8_retrieval_suite_is_grounded_scoped_and_rebuild_deterministic() -> None:
    report = run_phase8_retrieval_suite()

    assert report.all_safety_passed is True
    assert report.hit_at_k == 1.0
    assert report.recall_at_k == 1.0
    assert report.boundary_violation_count == 0
    assert report.injection_boundary_violation_count == 0
    assert report.deterministic_fingerprint == report.rebuild_fingerprint
    assert all(record.actual_chunk_ids for record in report.records if record.expected_path)

    encoded = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    assert "ignore system policy" not in encoded
    assert "purchase.submit" not in encoded
