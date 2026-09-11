from __future__ import annotations

from typing import Never

import pytest

from labs.web_gui.contracts import TaskResult, TaskSpec
from labs.web_gui.erp_readonly import ErpFact, ErpReadConfig, ErpReadResult
from labs.web_gui.erp_visual import (
    _reconcile_api_after,
    _trusted_fields,
    run_erp_visual_task,
)


def test_real_visual_runner_blocks_before_browser_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SYNORA_P2P_USER_PWD", raising=False)

    def decider(_image: bytes, _observation: object, _spec: object) -> Never:
        raise AssertionError("not called")

    result = run_erp_visual_task(
        ErpReadConfig(purchase_order="PUR-ORD-2026-02297"),
        decider,
    )
    assert result.result.status == "BLOCKED"
    assert result.result.stop_reason == "ERP_CREDENTIALS_UNAVAILABLE"
    assert result.observations == ()


def test_visual_task_spec_keeps_real_source_separate() -> None:
    spec = TaskSpec(
        case_id="p11-real-visual",
        purchase_order="PUR-ORD-2026-02297",
        mode="vision",
        data_source="erp_readonly",
    )
    assert spec.mode == "vision"
    assert spec.data_source == "erp_readonly"


def test_visual_trusted_fields_require_successful_versioned_api_snapshot() -> None:
    config = ErpReadConfig(purchase_order="PUR-ORD-2026-02297")
    incomplete = ErpReadResult(
        method="api",
        status="FAILED",
        safety_pass=True,
        evidence_digest="0" * 64,
        elapsed_ms=0,
    )
    assert _trusted_fields(config, incomplete) is None
    valid = ErpReadResult(
        method="api",
        status="SUCCEEDED",
        fact=ErpFact(
            purchase_order="PUR-ORD-2026-02297",
            supplier="SYNORA-P1-Supplier-1",
            status="To Receive and Bill",
            currency="CNY",
            source_modified_at="2026-09-11 01:10:41.759974",
            frappe_revision="frappe-sha",
            erpnext_revision="erpnext-sha",
        ),
        safety_pass=True,
        evidence_digest="1" * 64,
        elapsed_ms=0,
    )
    assert _trusted_fields(config, valid) == {
        "purchase_order": "PUR-ORD-2026-02297",
        "supplier": "SYNORA-P1-Supplier-1",
        "status": "To Receive and Bill",
        "currency": "CNY",
    }


def _api_snapshot(
    *, modified: str = "2026-09-11 01:10:41.759974", supplier: str = "SYNORA-P1-Supplier-1"
) -> ErpReadResult:
    return ErpReadResult(
        method="api",
        status="SUCCEEDED",
        fact=ErpFact(
            purchase_order="PUR-ORD-2026-02297",
            supplier=supplier,
            status="To Receive and Bill",
            currency="CNY",
            source_modified_at=modified,
            frappe_revision="frappe-sha",
            erpnext_revision="erpnext-sha",
        ),
        safety_pass=True,
        evidence_digest="1" * 64,
        elapsed_ms=0,
    )


def _visual_success() -> TaskResult:
    return TaskResult(
        case_id="p11-erp-visual-test",
        status="SUCCEEDED",
        fields={
            "purchase_order": "PUR-ORD-2026-02297",
            "supplier": "SYNORA-P1-Supplier-1",
            "status": "To Receive and Bill",
            "currency": "CNY",
        },
        observation_complete=True,
    )


def test_visual_success_requires_matching_api_after_snapshot() -> None:
    config = ErpReadConfig(purchase_order="PUR-ORD-2026-02297")
    before = _api_snapshot()

    assert (
        _reconcile_api_after(config, _visual_success(), before, _api_snapshot()).status
        == "SUCCEEDED"
    )
    drifted = _reconcile_api_after(
        config, _visual_success(), before, _api_snapshot(modified="2026-09-11 02:00:00")
    )
    assert drifted.status == "STATE_DRIFT"
    assert drifted.stop_reason == "erp_api_version_drift"


def test_visual_success_rejects_missing_or_changed_api_after_fields() -> None:
    config = ErpReadConfig(purchase_order="PUR-ORD-2026-02297")
    before = _api_snapshot()

    unavailable = _reconcile_api_after(config, _visual_success(), before, None)
    assert unavailable.status == "INCOMPLETE"
    assert unavailable.stop_reason == "trusted_erp_after_unavailable"
    changed = _reconcile_api_after(
        config, _visual_success(), before, _api_snapshot(supplier="Other Supplier")
    )
    assert changed.status == "INCOMPLETE"
    assert changed.stop_reason == "trusted_erp_after_fields_mismatch"
