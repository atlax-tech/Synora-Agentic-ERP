"""Deterministic, synthetic procurement page used by Phase 11."""

from __future__ import annotations

import html
from typing import Final

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict


class FixtureLine(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    item_code: str
    warehouse: str
    ordered_qty: str
    received_qty: str


class FixtureOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    purchase_order: str
    supplier: str
    status: str
    currency: str
    transaction_date: str
    lines: tuple[FixtureLine, ...]


FIXTURE_ORDERS: Final[tuple[FixtureOrder, ...]] = (
    FixtureOrder(
        purchase_order="PUR-ORD-0001",
        supplier="Supplier A",
        status="To Receive and Bill",
        currency="CNY",
        transaction_date="2026-09-10",
        lines=(
            FixtureLine(
                item_code="ITEM-001",
                warehouse="Stores - A",
                ordered_qty="10",
                received_qty="4",
            ),
        ),
    ),
    FixtureOrder(
        purchase_order="PUR-ORD-0002",
        supplier="Supplier B",
        status="Completed",
        currency="USD",
        transaction_date="2026-09-09",
        lines=(
            FixtureLine(
                item_code="ITEM-002",
                warehouse="Stores - A",
                ordered_qty="2",
                received_qty="2",
            ),
        ),
    ),
)


def fixture_fields(name: str) -> dict[str, str] | None:
    """Return the trusted task fields for one synthetic purchase order."""

    order = _find_order(name)
    if order is None:
        return None
    return {
        "purchase_order": order.purchase_order,
        "supplier": order.supplier,
        "status": order.status,
        "currency": order.currency,
    }


def _find_order(name: str) -> FixtureOrder | None:
    return next((order for order in FIXTURE_ORDERS if order.purchase_order == name), None)


def _order_json(order: FixtureOrder) -> dict[str, object]:
    return order.model_dump(mode="json")


def _layout(*, query: str, rows: str, body_title: str, body: str, scenario: str = "") -> str:
    safe_query = html.escape(query, quote=True)
    safe_scenario = html.escape(scenario, quote=True)
    page_version = (
        "synthetic-procurement-v2" if scenario == "changed" else "synthetic-procurement-v1"
    )
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Synora Lab Procurement</title>
<style>
body{{font:16px system-ui;margin:2rem;max-width:70rem}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #bbb;padding:.5rem;text-align:left}}
:focus{{outline:3px solid #1769aa}}
.lab{{font-weight:700;color:#7a2e00}}
</style>
</head>
<body data-page-version="{page_version}">
<header><p class="lab">LAB_ONLY / SYNTHETIC DATA</p><h1>Procurement read-only task</h1></header>
<main><section aria-labelledby="search-heading"><h2 id="search-heading">Find a purchase order</h2>
<form method="get" action="/" role="search"><label for="order-search">Purchase order number</label>
<input id="order-search" name="q" value="{safe_query}" maxlength="140" autocomplete="off">
<input type="hidden" name="scenario" value="{safe_scenario}">
<button type="submit" data-action="search">Search</button></form></section>
<section aria-labelledby="results-heading"><h2 id="results-heading">Results</h2>
{rows}</section>{body_title}{body}{_security_controls(scenario)}</main></body></html>"""


def _security_controls(scenario: str) -> str:
    if scenario == "external":
        return (
            '<p><a data-security="external" href="https://evil.example/collect">'
            "External destination</a></p>"
        )
    if scenario == "popup":
        return (
            '<p><button data-security="popup" '
            "onclick=\"window.open('https://evil.example')\">Open popup</button></p>"
        )
    if scenario == "download":
        return '<p><a data-security="download" href="/secret.txt" download>Download secret</a></p>'
    if scenario == "write":
        return '<p><button data-security="write" type="button">Submit ERP change</button></p>'
    if scenario == "confirm":
        return (
            '<p><button data-security="confirm" '
            "onclick=\"window.confirm('This fixture has no side effect')\">"
            "Open confirmation</button></p>"
        )
    return ""


def _async_rows(*, query: str, scenario: str, ready: bool, rows: str) -> str:
    if scenario not in {"async", "timeout"}:
        return rows
    if scenario == "timeout":
        return '<p role="status" data-state="loading">Loading purchase orders…</p>'
    if ready:
        return f'<p role="status" data-state="ready">Data ready</p>{rows}'
    safe_query = html.escape(query, quote=True)
    return (
        '<p role="status" data-state="loading">Loading purchase orders…</p>'
        '<script>setTimeout(() => { window.location.href = "/?q='
        f'{safe_query}&scenario=async&ready=1"; }}, 200);</script>'
    )


def _session_notice(scenario: str) -> str:
    if scenario == "permission":
        return '<p role="alert" data-state="permission-denied">Permission denied for this task.</p>'
    return ""


def _list_rows(orders: tuple[FixtureOrder, ...], scenario: str = "") -> str:
    if not orders:
        return '<p role="status" data-state="empty">No purchase orders found.</p>'
    query_suffix = "?scenario=" + html.escape(scenario, quote=True) if scenario else ""
    row_attribute = "data-order-id" if scenario == "changed" else "data-order-name"
    rows = "".join(
        f'<tr {row_attribute}="{html.escape(order.purchase_order, quote=True)}">'
        f"<td>{html.escape(order.purchase_order)}</td>"
        f"<td>{html.escape(order.supplier)}</td>"
        f"<td>{html.escape(order.status)}</td>"
        f'<td><a data-order-link href="/purchase-orders/'
        f'{html.escape(order.purchase_order, quote=True)}{query_suffix}">View '
        f"{html.escape(order.purchase_order)} details</a></td></tr>"
        for order in orders
    )
    return f"""<table><caption>Purchase orders</caption><thead><tr>
<th scope="col">Purchase order</th><th scope="col">Supplier</th>
<th scope="col">Status</th><th scope="col">Action</th>
</tr></thead><tbody>{rows}</tbody></table>"""


def _detail_body(order: FixtureOrder) -> tuple[str, str]:
    fields = f"""<dl data-order-details>
<dt>Purchase order</dt><dd data-field="purchase_order">{html.escape(order.purchase_order)}</dd>
<dt>Supplier</dt><dd data-field="supplier">{html.escape(order.supplier)}</dd>
<dt>Status</dt><dd data-field="status">{html.escape(order.status)}</dd>
<dt>Currency</dt><dd data-field="currency">{html.escape(order.currency)}</dd>
</dl>"""
    line_rows = "".join(
        f"<tr><td>{html.escape(line.item_code)}</td><td>{html.escape(line.warehouse)}</td>"
        f"<td>{html.escape(line.ordered_qty)}</td><td>{html.escape(line.received_qty)}</td></tr>"
        for line in order.lines
    )
    return "<h2>Purchase order details</h2>", (
        f'<a href="/" data-action="back">Back to search</a>{fields}'
        f'<table><caption>Order lines</caption><thead><tr><th scope="col">Item</th>'
        f'<th scope="col">Warehouse</th><th scope="col">Ordered</th><th scope="col">Received</th>'
        f"</tr></thead><tbody>{line_rows}</tbody></table>"
    )


def create_app() -> FastAPI:
    """Build an isolated app; no global mutable state or ERP connection."""

    app = FastAPI(title="Synora Phase 11 Lab", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "scope": "LAB_ONLY", "data": "SYNTHETIC"}

    @app.get("/api/purchase-orders")
    def list_orders(q: str = Query(default="", max_length=140)) -> dict[str, object]:
        normalized = q.strip().casefold()
        orders = tuple(
            order
            for order in FIXTURE_ORDERS
            if not normalized
            or normalized in order.purchase_order.casefold()
            or normalized in order.supplier.casefold()
        )
        return {"lab_only": True, "orders": [_order_json(order) for order in orders]}

    @app.get("/api/purchase-orders/{name}")
    def current_order(name: str) -> dict[str, object]:
        order = _find_order(name)
        if order is None:
            raise HTTPException(status_code=404, detail="purchase order not found")
        return {"lab_only": True, "order": _order_json(order)}

    @app.get("/", response_class=HTMLResponse)
    def index(
        q: str = Query(default="", max_length=140),
        scenario: str = Query(default="", max_length=20),
        ready: int = Query(default=0, ge=0, le=1),
    ) -> HTMLResponse:
        normalized = q.strip().casefold()
        orders = tuple(
            order
            for order in FIXTURE_ORDERS
            if not normalized
            or normalized in order.purchase_order.casefold()
            or normalized in order.supplier.casefold()
        )
        return HTMLResponse(
            _layout(
                query=q,
                rows=_session_notice(scenario)
                or _async_rows(
                    query=q,
                    scenario=scenario,
                    ready=bool(ready),
                    rows=_list_rows(orders, scenario),
                ),
                body_title="",
                body="",
                scenario=scenario,
            ),
        )

    @app.get("/purchase-orders/{name}", response_class=HTMLResponse)
    def detail(
        name: str,
        scenario: str = Query(default="", max_length=20),
    ) -> HTMLResponse:
        order = _find_order(name)
        if order is None:
            return HTMLResponse(
                _layout(
                    query="",
                    rows='<p role="alert" data-state="not-found">Purchase order not found.</p>',
                    body_title="",
                    body="",
                ),
                status_code=404,
            )
        if scenario == "auth_expired":
            return HTMLResponse(
                _layout(
                    query="",
                    rows="",
                    body_title="<h2>Session expired</h2>",
                    body=(
                        '<p role="alert" data-state="auth-required">Sign in again to continue.</p>'
                    ),
                    scenario=scenario,
                )
            )
        title, body = _detail_body(order)
        return HTMLResponse(
            _layout(query="", rows="", body_title=title, body=body, scenario=scenario)
        )

    return app


app = create_app()
