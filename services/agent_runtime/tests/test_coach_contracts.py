"""T06 Coach contract and current ERP snapshot boundary tests."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from agent_runtime.agent.contracts import Action
from agent_runtime.agent.native_tool_calling import READ_TOOL_NAMES, provider_tool_specs
from agent_runtime.coach import (
    CoachContextError,
    CoachQuestionRequest,
    MaterialRequestCurrentFact,
    PurchaseOrderCurrentFact,
    build_current_document_context,
)
from agent_runtime.gateway import GatewaySuccess
from pydantic import ValidationError

RUN_ID = UUID("37e1d8a5-1730-4ad0-bffd-217774ed9fab")
CORRELATION_ID = UUID("1687c82a-4b61-4e6e-855a-a10ec3578b96")


def _request(**overrides: object) -> CoachQuestionRequest:
    values: dict[str, object] = {
        "schema_version": "1",
        "run_id": str(RUN_ID),
        "correlation_id": str(CORRELATION_ID),
        "question": "What is the current requested quantity?",
        "current_document": {"doctype": "Material Request", "name": "MAT-MR-0001"},
    }
    values.update(overrides)
    return CoachQuestionRequest.model_validate(values)


def _mr_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "material_request": "MAT-MR-0001",
        "company": "Acme",
        "material_request_type": "Purchase",
        "docstatus": 1,
        "status": "Pending",
        "transaction_date": "2026-08-30",
        "item_code": "ITEM-1",
        "warehouse": "Stores - A",
        "stock_uom": "Unit",
        "schedule_date": "2026-09-01",
        "requested_stock_qty": "3",
        "ordered_stock_qty": "1",
        "open_order_stock_qty": "2",
    }
    row.update(overrides)
    return row


def _po_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "purchase_order": "PUR-ORD-0001",
        "company": "Acme",
        "supplier": "Supplier-1",
        "currency": "CNY",
        "docstatus": 0,
        "status": "Draft",
        "transaction_date": "2026-08-30",
        "item_code": "ITEM-1",
        "warehouse": "Stores - A",
        "stock_uom": "Unit",
        "schedule_date": "2026-09-01",
        "ordered_stock_qty": "2",
        "received_stock_qty": "0",
        "open_receipt_stock_qty": "2",
    }
    row.update(overrides)
    return row


def _gateway(
    *,
    tool_name: str = "material_request.current",
    data: list[dict[str, Any]] | None = None,
    warehouse: str | None = "Stores - A",
    **overrides: object,
) -> GatewaySuccess:
    rows = data if data is not None else [_mr_row()]
    payload: dict[str, object] = {
        "ok": True,
        "schema_version": "1",
        "run_id": str(RUN_ID),
        "state_version": 3,
        "correlation_id": str(CORRELATION_ID),
        "tool": {
            "name": tool_name,
            "version": "1",
            "risk": "READ",
            "caller_authorization": "FRAPPE_PERMISSION_AND_RUN_SCOPE",
            "timeout_ms": 5_000,
            "max_page_size": 50,
        },
        "authorized_scope": {"company": "Acme", "warehouse": warehouse},
        "snapshot": {
            "captured_at": "2026-08-30 12:00:00",
            "source_modified_at": "2026-08-30 11:59:00",
            "frappe_revision": "f" * 40,
            "erpnext_revision": "e" * 40,
        },
        "completeness": {"status": "COMPLETE", "omissions": {}},
        "page": {"offset": 0, "limit": 50, "returned": len(rows), "has_more": False},
        "data": rows,
    }
    payload.update(overrides)
    return GatewaySuccess.model_validate(payload)


def test_question_request_is_strict_and_requires_canonical_identity() -> None:
    request = _request()
    assert request.run_id == RUN_ID
    assert request.current_document.name == "MAT-MR-0001"
    with pytest.raises(ValidationError):
        _request(current_document={"doctype": "Invoice", "name": "INV-1"})
    with pytest.raises(ValidationError):
        _request(question="   ")
    with pytest.raises(ValidationError):
        _request(question="q" * 1_001)
    with pytest.raises(ValidationError):
        _request(run_id=str(RUN_ID).upper())
    with pytest.raises(ValidationError):
        _request(unexpected=True)


def test_document_ref_rejects_blank_or_overlong_name() -> None:
    with pytest.raises(ValidationError):
        _request(current_document={"doctype": "Material Request", "name": " "})
    with pytest.raises(ValidationError):
        _request(current_document={"doctype": "Material Request", "name": "x" * 141})


def test_current_context_accepts_current_mr_and_enforces_identity_and_scope() -> None:
    context = build_current_document_context(_request(), _gateway())
    assert context.coverage == "WAREHOUSE_SCOPED"
    fact = context.facts[0]
    assert isinstance(fact, MaterialRequestCurrentFact)
    assert fact.material_request == "MAT-MR-0001"
    assert fact.open_order_stock_qty == "2"

    with pytest.raises(CoachContextError):
        build_current_document_context(_request(), _gateway(tool_name="purchase_order.current"))
    with pytest.raises(CoachContextError):
        build_current_document_context(
            _request(), _gateway(data=[_mr_row(material_request="MAT-MR-0002")])
        )
    with pytest.raises(CoachContextError):
        build_current_document_context(_request(), _gateway(data=[_mr_row(warehouse="Stores - B")]))


def test_current_context_accepts_po_draft_and_full_document_scope() -> None:
    request = _request(current_document={"doctype": "Purchase Order", "name": "PUR-ORD-0001"})
    context = build_current_document_context(
        request,
        _gateway(tool_name="purchase_order.current", data=[_po_row()], warehouse=None),
    )
    assert context.coverage == "FULL_DOCUMENT"
    fact = context.facts[0]
    assert isinstance(fact, PurchaseOrderCurrentFact)
    assert fact.purchase_order == "PUR-ORD-0001"
    assert fact.docstatus == 0


def test_current_context_rejects_incomplete_pages_bad_quantities_and_cross_types() -> None:
    request = _request()
    with pytest.raises(CoachContextError):
        build_current_document_context(
            request,
            _gateway(page={"offset": 0, "limit": 50, "returned": 1, "has_more": True}),
        )
    with pytest.raises(CoachContextError):
        build_current_document_context(
            request,
            _gateway(data=[_mr_row(open_order_stock_qty="-1")]),
        )
    with pytest.raises(CoachContextError):
        build_current_document_context(request, _gateway(data=[_po_row()]))


def test_current_tools_are_not_provider_or_agent_callable() -> None:
    assert "material_request.current" not in READ_TOOL_NAMES
    assert "purchase_order.current" not in READ_TOOL_NAMES
    specs = provider_tool_specs(frozenset(READ_TOOL_NAMES))
    assert {spec.name for spec in specs}.isdisjoint(
        {"material_request.current", "purchase_order.current"}
    )
    with pytest.raises(ValidationError):
        Action.model_validate(
            {
                "step": 1,
                "tool_name": "material_request.current",
                "canonical_args": {"name": "MAT-MR-0001"},
                "correlation_id": CORRELATION_ID,
            }
        )
