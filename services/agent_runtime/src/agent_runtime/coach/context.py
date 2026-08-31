"""Convert one internal Gateway current-document response into Coach context."""

from __future__ import annotations

import hashlib

from agent_runtime.agent.context import ContextFragment
from agent_runtime.agent.contracts import canonical_json
from agent_runtime.coach.contracts import (
    CoachCurrentDocumentContext,
    CoachQuestionRequest,
)
from agent_runtime.gateway import GatewaySuccess


class CoachContextError(ValueError):
    """The live snapshot cannot safely be used as Coach context."""


_EXPECTED_TOOLS = {
    "Material Request": "material_request.current",
    "Purchase Order": "purchase_order.current",
}


def build_current_document_context(
    request: CoachQuestionRequest,
    gateway: GatewaySuccess,
) -> CoachCurrentDocumentContext:
    """Validate identity, tool provenance, page completeness and row scope."""

    expected_tool = _EXPECTED_TOOLS[request.current_document.doctype]
    if (
        gateway.schema_version != "1"
        or gateway.tool.name != expected_tool
        or gateway.tool.version != "1"
        or gateway.tool.risk != "READ"
    ):
        raise CoachContextError("gateway tool is not the expected read-only current tool")
    if gateway.run_id != request.run_id or gateway.correlation_id != request.correlation_id:
        raise CoachContextError("gateway identity does not match Coach request")
    if gateway.page.offset != 0 or gateway.page.has_more:
        raise CoachContextError("current document snapshot is incomplete")
    if gateway.page.returned != len(gateway.data):
        raise CoachContextError("current document page count is invalid")
    payload = {
        "run_id": request.run_id,
        "state_version": gateway.state_version,
        "current_document": request.current_document,
        "authorized_company": gateway.authorized_scope.company,
        "authorized_warehouse": gateway.authorized_scope.warehouse,
        "coverage": ("WAREHOUSE_SCOPED" if gateway.authorized_scope.warehouse else "FULL_DOCUMENT"),
        "captured_at": gateway.snapshot.captured_at,
        "source_modified_at": gateway.snapshot.source_modified_at,
        "frappe_revision": gateway.snapshot.frappe_revision,
        "erpnext_revision": gateway.snapshot.erpnext_revision,
        "facts": tuple(gateway.data),
    }
    try:
        return CoachCurrentDocumentContext.model_validate(payload)
    except Exception as error:
        raise CoachContextError("current document facts are invalid") from error


def current_fact_digest(fact: object) -> str:
    """Digest the canonical typed representation of one current ERP fact."""
    if not hasattr(fact, "model_dump"):
        raise CoachContextError("current ERP fact is not typed")
    try:
        payload = fact.model_dump(mode="json")
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    except Exception as error:
        raise CoachContextError("current ERP fact cannot be digested") from error


def current_context_to_fragment(
    request: CoachQuestionRequest,
    context: CoachCurrentDocumentContext,
) -> ContextFragment:
    """Build a required CONTROLLED fragment for the server-selected snapshot."""
    if request.run_id != context.run_id or request.current_document != context.current_document:
        raise CoachContextError("current context identity does not match Coach request")
    facts = [
        {
            "fact_digest": current_fact_digest(fact),
            "fact": fact.model_dump(mode="json"),
        }
        for fact in context.facts
    ]
    payload = {
        "evidence_type": "LIVE_ERP",
        "run_id": str(context.run_id),
        "document": context.current_document.model_dump(mode="json"),
        "scope": {
            "company": context.authorized_company,
            "warehouse": context.authorized_warehouse,
            "coverage": context.coverage,
        },
        "state_version": context.state_version,
        "captured_at": context.captured_at,
        "source_modified_at": context.source_modified_at,
        "frappe_revision": context.frappe_revision,
        "erpnext_revision": context.erpnext_revision,
        "facts": facts,
    }
    content = canonical_json(payload)
    document_slug = (
        "material-request"
        if context.current_document.doctype == "Material Request"
        else "purchase-order"
    )
    fragment_id = (
        f"erp.current:{document_slug}:{hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]}"
    )
    return ContextFragment.from_content(
        fragment_id=fragment_id,
        fragment_type="reference",
        source=f"erp:current:{document_slug}",
        version=f"state-{context.state_version}",
        trust_level="CONTROLLED",
        priority=900,
        content=content,
        required=True,
    )


__all__ = [
    "CoachContextError",
    "build_current_document_context",
    "current_context_to_fragment",
    "current_fact_digest",
]
