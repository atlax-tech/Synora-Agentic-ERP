"""T06 Coach contract and current ERP snapshot boundary tests."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from agent_runtime.agent.contracts import Action
from agent_runtime.agent.native_tool_calling import READ_TOOL_NAMES, provider_tool_specs
from agent_runtime.coach import (
    CoachAnswer,
    CoachClaim,
    CoachContextError,
    CoachLiveCitation,
    CoachProviderOutput,
    CoachQuestionRequest,
    MaterialRequestCurrentFact,
    PurchaseOrderCurrentFact,
    build_current_document_context,
    parse_coach_provider_output,
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


def _live_citation(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "citation_type": "LIVE_ERP",
        "citation_id": "live-1",
        "run_id": str(RUN_ID),
        "document_doctype": "Material Request",
        "document_name": "MAT-MR-0001",
        "state_version": 3,
        "captured_at": "2026-08-30 12:00:00",
        "source_modified_at": "2026-08-30 11:59:00",
        "fact_digest": "a" * 64,
    }
    value.update(overrides)
    return value


def _retrieval_citation(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "citation_type": "RETRIEVAL",
        "citation_id": "retrieval-1",
        "chunk_id": "b" * 64,
        "content_digest": "c" * 64,
        "ordinal": 1,
        "source_type": "sop",
        "revision": "v1",
        "erp_version": "erp-a",
        "permission_scope": "internal",
    }
    value.update(overrides)
    return value


def test_coach_output_is_strict_and_citations_are_resolved_by_claim_refs() -> None:
    output = CoachProviderOutput.model_validate(
        {
            "schema_version": "1",
            "answer_status": "ANSWERED",
            "answer": "The request has two open units.",
            "claims": [
                {
                    "claim_id": "claim-1",
                    "ordinal": 1,
                    "claim_type": "ERP_FACT",
                    "text": "Two units remain open.",
                    "citation_refs": ["live-1"],
                }
            ],
            "citations": [_live_citation()],
            "refusal_reason": None,
        }
    )
    assert output.claims[0].citation_refs == ("live-1",)
    assert output.citations[0].citation_id == "live-1"
    assert CoachClaim.model_validate(output.claims[0].model_dump())
    assert CoachLiveCitation.model_validate(output.citations[0].model_dump())

    with pytest.raises(ValidationError):
        CoachProviderOutput.model_validate(
            {
                "schema_version": "1",
                "answer_status": "ANSWERED",
                "answer": "unsupported",
                "claims": [],
                "citations": [],
                "refusal_reason": None,
                "unexpected": True,
            }
        )
    with pytest.raises(ValidationError):
        CoachProviderOutput.model_validate(
            {
                "schema_version": "1",
                "answer_status": "ANSWERED",
                "answer": "unsupported",
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "ordinal": 1,
                        "claim_type": "ERP_FACT",
                        "text": "uncited",
                        "citation_refs": ["missing"],
                    }
                ],
                "citations": [],
                "refusal_reason": None,
            }
        )
    with pytest.raises(ValidationError):
        CoachProviderOutput.model_validate(
            {
                "schema_version": "1",
                "answer_status": "ANSWERED",
                "answer": "unsupported",
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "ordinal": 1,
                        "claim_type": "ERP_FACT",
                        "text": "duplicate refs",
                        "citation_refs": ["live-1"],
                    }
                ],
                "citations": [_live_citation(), _live_citation(citation_id="live-1")],
                "refusal_reason": None,
            }
        )


def test_provider_json_parser_rejects_duplicate_keys_and_unknown_citation_types() -> None:
    valid = (
        '{"schema_version":"1","answer_status":"REFUSED",'
        '"answer":"","claims":[],"citations":[],"refusal_reason":"not enough evidence"}'
    )
    parsed = parse_coach_provider_output(valid)
    assert parsed.answer_status == "REFUSED"
    with pytest.raises(ValueError):
        parse_coach_provider_output(valid.replace('"answer":""', '"answer":"","answer":"x"'))
    with pytest.raises(ValidationError):
        CoachProviderOutput.model_validate(
            {
                "schema_version": "1",
                "answer_status": "ANSWERED",
                "answer": "bad citation",
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "ordinal": 1,
                        "claim_type": "ERP_FACT",
                        "text": "bad",
                        "citation_refs": ["unknown"],
                    }
                ],
                "citations": [
                    {
                        **_retrieval_citation(),
                        "citation_type": "TOOL",
                        "citation_id": "unknown",
                    }
                ],
                "refusal_reason": None,
            }
        )


def test_coach_answer_requires_bounded_usage_and_trace() -> None:
    with pytest.raises(ValidationError):
        CoachAnswer.model_validate(
            {
                "schema_version": "1",
                "answer_status": "REFUSED",
                "answer": "",
                "claims": [],
                "citations": [],
                "refusal_reason": "no evidence",
                "retrieval_trace": {"selected_chunk_ids": ["bad"]},
                "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0},
                "latency_ms": 0,
            }
        )
