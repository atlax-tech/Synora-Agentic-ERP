from __future__ import annotations

import asyncio

import httpx
import pytest

from labs.web_gui.erp_readonly import (
    MAX_RESPONSE_BYTES,
    ErpFact,
    ErpReadConfig,
    _fact_from_data,
    _parse_issue_run,
    read_erp_api,
)


def test_real_erp_config_is_loopback_only() -> None:
    config = ErpReadConfig(purchase_order="PUR-ORD-2026-02297")
    assert config.base_url == "http://127.0.0.1:8000"
    with pytest.raises(ValueError):
        ErpReadConfig(base_url="https://erp.example.test", purchase_order="PO-1")
    with pytest.raises(ValueError):
        ErpReadConfig(base_url="http://127.0.0.1:8000/?token=secret", purchase_order="PO-1")
    with pytest.raises(ValueError):
        ErpReadConfig(purchase_order="PUR-ORD-2026/02297")


def test_issue_run_and_fact_parsers_fail_closed() -> None:
    valid = {
        "message": {
            "ok": True,
            "run": {"run_id": "run-id", "capability": "capability"},
        }
    }
    assert _parse_issue_run(valid) == ("run-id", "capability")
    assert _parse_issue_run({"message": {"ok": True}}) is None
    snapshot = {
        "source_modified_at": "2026-09-11 01:10:41",
        "frappe_revision": "frappe-sha",
        "erpnext_revision": "erpnext-sha",
    }
    fact = _fact_from_data(
        [
            {
                "purchase_order": "PUR-ORD-2026-02297",
                "supplier": "SYNORA-P1-Supplier-1",
                "status": "To Receive and Bill",
                "currency": "CNY",
            }
        ],
        "PUR-ORD-2026-02297",
        snapshot,
    )
    assert fact == ErpFact(
        purchase_order="PUR-ORD-2026-02297",
        supplier="SYNORA-P1-Supplier-1",
        status="To Receive and Bill",
        currency="CNY",
        source_modified_at="2026-09-11 01:10:41",
        frappe_revision="frappe-sha",
        erpnext_revision="erpnext-sha",
    )
    assert _fact_from_data([], "PUR-ORD-2026-02297", snapshot) is None
    assert (
        _fact_from_data(
            [{"purchase_order": "OTHER", "supplier": "S", "status": "Draft", "currency": "CNY"}],
            "PUR-ORD-2026-02297",
            snapshot,
        )
        is None
    )


def test_real_api_reader_reports_missing_credentials_without_network() -> None:
    config = ErpReadConfig(purchase_order="PUR-ORD-2026-02297")
    result = asyncio.run(read_erp_api(config, environ={}))
    assert result.status == "BLOCKED"
    assert result.failure_code == "ERP_CREDENTIALS_UNAVAILABLE"
    assert result.safety_pass


def test_real_api_reader_rejects_oversized_login_response() -> None:
    config = ErpReadConfig(purchase_order="PUR-ORD-2026-02297")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))

    result = asyncio.run(
        read_erp_api(
            config,
            environ={"SYNORA_P2P_USER_PWD": "test-only"},
            transport=httpx.MockTransport(handler),
        )
    )
    assert result.status == "FAILED"
    assert result.failure_code == "ERP_RESPONSE_TOO_LARGE"
