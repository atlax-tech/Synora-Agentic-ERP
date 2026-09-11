from __future__ import annotations

import asyncio

from labs.web_gui.erp_browser import _RealPolicy, _response_body_too_large, read_erp_web
from labs.web_gui.erp_readonly import ERP_GATEWAY_PATH, ErpReadConfig


def test_real_policy_allows_observed_read_requests_only() -> None:
    policy = _RealPolicy("http://127.0.0.1:8000", "PUR-ORD-2026-02297")
    assert policy.check("GET", "http://127.0.0.1:8000/desk/purchase-order/PUR-ORD-2026-02297")
    assert policy.check(
        "GET",
        "http://127.0.0.1:8000/api/method/frappe.desk.form.load.getdoc"
        "?doctype=Purchase%20Order&name=PUR-ORD-2026-02297&_=1",
    )
    assert policy.check(
        "GET",
        "http://127.0.0.1:8000/api/method/frappe.desk.form.load.getdoctype"
        "?doctype=Purchase%20Order&with_parent=1&_=2",
    )
    assert not policy.check("POST", ERP_GATEWAY_PATH)
    assert not policy.check("GET", "http://127.0.0.1:8000/desk/purchase-order/other")
    assert not policy.check(
        "GET", "http://evil.example.test/desk/purchase-order/PUR-ORD-2026-02297"
    )
    assert not policy.check("GET", "http://127.0.0.1:9000/socket.io/?transport=polling")
    assert "REQUEST_NOT_ALLOWLISTED" in policy.violations
    assert "ORIGIN_NOT_ALLOWED" in policy.violations


def test_real_web_reader_reports_missing_credentials_without_network() -> None:
    config = ErpReadConfig(purchase_order="PUR-ORD-2026-02297")
    result = asyncio.run(read_erp_web(config, environ={}))
    assert result.status == "BLOCKED"
    assert result.failure_code == "ERP_CREDENTIALS_UNAVAILABLE"
    assert result.safety_pass


def test_response_limit_checks_body_when_content_length_is_missing_or_invalid() -> None:
    assert _response_body_too_large(b"x" * (2_000_000 + 1), {})
    assert _response_body_too_large(b"ok", {"content-length": "not-a-number"}) is False
