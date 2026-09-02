"""Offline contract checks for the tracked Phase 8 real acceptance runner."""

from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "phase8_coach_acceptance.py"
SPEC = Path(__file__).parents[1] / "scripts" / "phase8_coach_cases.json"


def _runner() -> ModuleType:
    module_spec = importlib.util.spec_from_file_location("phase8_coach_acceptance", SCRIPT)
    assert module_spec and module_spec.loader
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def test_fixed_case_spec_and_hash_are_stable() -> None:
    runner = _runner()
    spec, case_spec_sha = runner.load_case_spec(SPEC)

    assert tuple(spec["case_order"]) == runner.EXPECTED_ORDER
    assert len(spec["cases"]) == 12
    assert case_spec_sha == runner.digest(runner.case_spec_payload(spec))
    assert {case["id"] for case in spec["cases"]} == set(runner.EXPECTED_ORDER)


def test_manifest_binds_head_worktree_and_parent_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner()
    spec, case_spec_sha = runner.load_case_spec(SPEC)
    monkeypatch.setattr(runner, "git_status", lambda: " M unrelated.txt\n")
    monkeypatch.setattr(
        runner,
        "current_git",
        lambda: {"branch": "main", "head": "abc123"},
    )
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    monkeypatch.setenv("OLLAMA_API_KEY", "secret-that-must-not-be-emitted")

    manifest = runner.build_manifest(spec, case_spec_sha, "a" * 64)
    raw = json.dumps(manifest, ensure_ascii=False)

    assert manifest["baseline"] == {
        "branch": "main",
        "head": "abc123",
        "status_sha256": runner.digest_bytes(b" M unrelated.txt\n"),
        "repo_evaluation_changes": "none",
    }
    assert manifest["parent_manifest_sha256"] == "a" * 64
    assert "secret-that-must-not-be-emitted" not in raw
    assert manifest["environment_snapshot"]["roles"]["primary"]["api_key_present"] is True


def test_manifest_validation_rejects_case_order_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _runner()
    spec, case_spec_sha = runner.load_case_spec(SPEC)
    monkeypatch.setattr(runner, "git_status", lambda: "")
    monkeypatch.setattr(
        runner,
        "current_git",
        lambda: {"branch": "main", "head": "abc123"},
    )
    manifest = runner.build_manifest(spec, case_spec_sha, "b" * 64)
    manifest["case_order"] = list(reversed(runner.EXPECTED_ORDER))

    with pytest.raises(runner.Blocked, match="manifest_order_mismatch"):
        runner.validate_manifest(manifest, spec)


def test_immutable_write_is_read_only_and_answer_is_redacted(tmp_path: Path) -> None:
    runner = _runner()
    output = tmp_path / "result.json"
    value = {
        "coach": {
            "answer": "ERP answer that stays out of the evidence file",
            "answer_digest": "digest",
        },
    }
    runner.write_immutable(output, runner._redact_case(value))
    loaded = json.loads(output.read_text(encoding="utf-8"))

    assert "answer" not in loaded["coach"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o444
    with pytest.raises(runner.Blocked, match="immutable_output_already_exists"):
        runner.write_immutable(output, value)


def test_c3_safe_refusal_is_scored_separately_from_mixed_citations() -> None:
    runner = _runner()
    spec, _ = runner.load_case_spec(SPEC)
    anchor = spec["anchors"]["MR"]
    result = {
        "id": "C3",
        "verdict": "PASS",
        "coach": {
            "answer_status": "UNKNOWN",
            "answer": "",
            "claims": [],
            "citations": [],
            "refusal_reason": "insufficient evidence",
            "retrieval_trace": {"provider_tools": [], "selected_chunk_ids": []},
        },
    }

    runner.score_case(result, anchor, {"id": "C3"}, spec)

    assert result["verdict"] == "PASS"
    assert result["citation_evidence_mode"] == "SAFE_REFUSAL"
    assert result["checks"]["safe_refusal_explicit"] is True
    assert "citation_graph_separated" not in result["checks"]
