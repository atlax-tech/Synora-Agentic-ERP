"""Pure tests for the Phase 6 read-only approval mapping probe."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROBE = ROOT / "approval_mapping_probe.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location("approval_mapping_probe", PROBE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load probe")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_has_no_business_write_calls() -> None:
    tree = ast.parse(PROBE.read_text(encoding="utf-8"))
    forbidden = {"insert", "save", "submit", "delete", "set_value", "sql"}
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert calls.isdisjoint(forbidden), sorted(calls & forbidden)


def test_probe_output_schema_rejects_unknown_top_level_and_sensitive_keys() -> None:
    probe = _load_probe()
    base = {
        "schema_version": "1",
        "probe": "approval-workflow-mapping",
        "target_doctypes": ["Material Request", "Purchase Order"],
    }
    incomplete = dict(base)
    try:
        probe.validate_probe_output(incomplete)
    except ValueError:
        pass
    else:
        raise AssertionError("incomplete output must fail closed")
    complete = {key: [] for key in probe._REQUIRED_TOP_LEVEL_KEYS}
    complete.update(base)
    probe.validate_probe_output(complete)
    unknown = dict(base, unexpected=True)
    try:
        probe.validate_probe_output(unknown)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown output field must fail closed")
    sensitive = dict(base, configuration={"capability": "never"})
    try:
        probe.validate_probe_output(sensitive)
    except ValueError:
        pass
    else:
        raise AssertionError("sensitive output field must fail closed")


def test_probe_sorting_and_user_redaction_are_stable() -> None:
    probe = _load_probe()
    rows = [{"role": "Stock User", "idx": 2}, {"role": "Purchase User", "idx": 1}]
    assert probe._stable_rows(rows, ("role", "idx"))[0]["role"] == "Purchase User"
    assert set(probe.USER_ALIASES.values()) == {
        "buyer",
        "approver",
        "receiver",
        "accountant",
        "viewer",
        "company_a_only",
    }
    assert all("@" not in alias for alias in probe.USER_ALIASES.values())


def test_probe_targets_only_phase6_draft_documents() -> None:
    probe = _load_probe()
    assert probe.TARGET_DOCTYPES == ("Material Request", "Purchase Order")
    assert "Purchase Receipt" not in probe.TARGET_DOCTYPES
    assert "Purchase Invoice" not in probe.TARGET_DOCTYPES
