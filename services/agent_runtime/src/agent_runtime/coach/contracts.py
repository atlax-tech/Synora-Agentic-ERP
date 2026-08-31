"""Strict, provider-neutral live context contracts for Coach T06.

Only the user's question identity and one freshly authorized ERP snapshot are
represented here.  Claims, citations, retrieval evidence, Memory and provider
output are deliberately absent until the following Coach task.
"""

from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from agent_runtime.agent.contracts import StrictModel

CoachDocumentType = Literal["Material Request", "Purchase Order"]
CoachCoverage = Literal["FULL_DOCUMENT", "WAREHOUSE_SCOPED"]
_IDENTIFIER = Field(min_length=1, max_length=140)
_QUANTITY_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")


def _nonblank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


def _canonical_uuid(value: object, field_name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a canonical UUID")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must be a canonical UUID") from error
    if value != str(parsed):
        raise ValueError(f"{field_name} must be a canonical UUID")
    return parsed


def _quantity(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not _QUANTITY_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a non-negative decimal string")
    return value


class CoachDocumentRef(StrictModel):
    doctype: CoachDocumentType
    name: str = _IDENTIFIER

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _nonblank(value, "name")


class CoachQuestionRequest(StrictModel):
    schema_version: Literal["1"] = "1"
    run_id: UUID
    correlation_id: UUID
    question: str = Field(min_length=1, max_length=1_000)
    current_document: CoachDocumentRef

    @field_validator("run_id", "correlation_id", mode="before")
    @classmethod
    def validate_uuid(cls, value: object, info: object) -> UUID:
        field_name = getattr(info, "field_name", "uuid")
        return _canonical_uuid(value, str(field_name))

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        return _nonblank(value, "question")


class _CurrentFact(StrictModel):
    company: str = _IDENTIFIER
    docstatus: int = Field(ge=0, le=2)
    status: str = _IDENTIFIER
    transaction_date: str = _IDENTIFIER
    item_code: str | None = Field(default=None, max_length=140)
    warehouse: str | None = Field(default=None, max_length=140)
    stock_uom: str | None = Field(default=None, max_length=140)
    schedule_date: str | None = Field(default=None, max_length=140)

    @field_validator("company", "status", "transaction_date")
    @classmethod
    def validate_parent_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "field")
        return _nonblank(value, str(field_name))

    @field_validator("item_code", "warehouse", "stock_uom", "schedule_date")
    @classmethod
    def validate_optional_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "field")
        return _nonblank(value, str(field_name))


class MaterialRequestCurrentFact(_CurrentFact):
    material_request: str = _IDENTIFIER
    material_request_type: str = _IDENTIFIER
    requested_stock_qty: str | None = Field(default=None, max_length=80)
    ordered_stock_qty: str | None = Field(default=None, max_length=80)
    open_order_stock_qty: str | None = Field(default=None, max_length=80)

    @field_validator("material_request", "material_request_type")
    @classmethod
    def validate_specific_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "field")
        return _nonblank(value, str(field_name))

    @field_validator("requested_stock_qty", "ordered_stock_qty", "open_order_stock_qty")
    @classmethod
    def validate_quantities(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "quantity")
        return _quantity(value, str(field_name))


class PurchaseOrderCurrentFact(_CurrentFact):
    purchase_order: str = _IDENTIFIER
    supplier: str = _IDENTIFIER
    currency: str = _IDENTIFIER
    ordered_stock_qty: str | None = Field(default=None, max_length=80)
    received_stock_qty: str | None = Field(default=None, max_length=80)
    open_receipt_stock_qty: str | None = Field(default=None, max_length=80)

    @field_validator("purchase_order", "supplier", "currency")
    @classmethod
    def validate_specific_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "field")
        return _nonblank(value, str(field_name))

    @field_validator("ordered_stock_qty", "received_stock_qty", "open_receipt_stock_qty")
    @classmethod
    def validate_quantities(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "quantity")
        return _quantity(value, str(field_name))


class CoachCurrentDocumentContext(StrictModel):
    schema_version: Literal["1"] = "1"
    run_id: UUID
    state_version: int = Field(ge=1, le=1_000_000)
    current_document: CoachDocumentRef
    authorized_company: str = _IDENTIFIER
    authorized_warehouse: str | None = Field(default=None, max_length=140)
    coverage: CoachCoverage
    captured_at: str = _IDENTIFIER
    source_modified_at: str | None = Field(default=None, max_length=140)
    facts: tuple[MaterialRequestCurrentFact | PurchaseOrderCurrentFact, ...] = Field(max_length=50)

    @field_validator("run_id", mode="before")
    @classmethod
    def validate_run_uuid(cls, value: object) -> UUID:
        return _canonical_uuid(value, "run_id")

    @field_validator("authorized_company", "captured_at")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "field")
        return _nonblank(value, str(field_name))

    @field_validator("authorized_warehouse", "source_modified_at")
    @classmethod
    def validate_optional_metadata(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "field")
        return _nonblank(value, str(field_name))

    @model_validator(mode="after")
    def validate_scope_and_fact_identity(self) -> CoachCurrentDocumentContext:
        expected_coverage = "WAREHOUSE_SCOPED" if self.authorized_warehouse else "FULL_DOCUMENT"
        if self.coverage != expected_coverage:
            raise ValueError("coverage does not match authorized warehouse scope")
        for fact in self.facts:
            if self.current_document.doctype == "Material Request":
                if not isinstance(fact, MaterialRequestCurrentFact):
                    raise ValueError("fact type does not match current document")
                if fact.material_request != self.current_document.name:
                    raise ValueError("fact belongs to another material request")
            else:
                if not isinstance(fact, PurchaseOrderCurrentFact):
                    raise ValueError("fact type does not match current document")
                if fact.purchase_order != self.current_document.name:
                    raise ValueError("fact belongs to another purchase order")
            if fact.company != self.authorized_company:
                raise ValueError("fact company does not match authorized scope")
            if self.authorized_warehouse and fact.warehouse not in {
                None,
                self.authorized_warehouse,
            }:
                raise ValueError("fact warehouse is outside authorized scope")
        return self


__all__ = [
    "CoachCoverage",
    "CoachCurrentDocumentContext",
    "CoachDocumentRef",
    "CoachDocumentType",
    "CoachQuestionRequest",
    "MaterialRequestCurrentFact",
    "PurchaseOrderCurrentFact",
]
