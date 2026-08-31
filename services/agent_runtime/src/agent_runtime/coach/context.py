"""Convert one internal Gateway current-document response into Coach context."""

from __future__ import annotations

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
        "facts": tuple(gateway.data),
    }
    try:
        return CoachCurrentDocumentContext.model_validate(payload)
    except Exception as error:
        raise CoachContextError("current document facts are invalid") from error


__all__ = ["CoachContextError", "build_current_document_context"]
