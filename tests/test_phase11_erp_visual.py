from __future__ import annotations

from typing import Never

import pytest

from labs.web_gui.contracts import TaskSpec
from labs.web_gui.erp_readonly import ErpFact, ErpReadConfig, ErpReadResult
from labs.web_gui.erp_visual import _trusted_fields, run_erp_visual_task


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
