"""Provider-neutral contracts for the contextual ERP Coach."""

from agent_runtime.coach.context import CoachContextError, build_current_document_context
from agent_runtime.coach.contracts import (
    CoachCurrentDocumentContext,
    CoachDocumentRef,
    CoachQuestionRequest,
    MaterialRequestCurrentFact,
    PurchaseOrderCurrentFact,
)

__all__ = [
    "CoachContextError",
    "CoachCurrentDocumentContext",
    "CoachDocumentRef",
    "CoachQuestionRequest",
    "MaterialRequestCurrentFact",
    "PurchaseOrderCurrentFact",
    "build_current_document_context",
]
