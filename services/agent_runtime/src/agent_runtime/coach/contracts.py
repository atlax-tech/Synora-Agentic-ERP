"""Strict, provider-neutral contracts for the contextual ERP Coach.

The provider output is still untrusted text.  These models describe the only
shape that may cross the runtime boundary; the service subsequently resolves
each citation against the current ERP snapshot and the selected retrieval
hits.  No model-generated identifier is an authority by itself.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from agent_runtime.agent.contracts import StrictModel, canonical_json

CoachDocumentType = Literal["Material Request", "Purchase Order"]
CoachCoverage = Literal["FULL_DOCUMENT", "WAREHOUSE_SCOPED"]
CoachLiveFactField = Literal[
    "company",
    "docstatus",
    "status",
    "transaction_date",
    "item_code",
    "warehouse",
    "stock_uom",
    "schedule_date",
    "material_request",
    "material_request_type",
    "requested_stock_qty",
    "ordered_stock_qty",
    "open_order_stock_qty",
    "purchase_order",
    "supplier",
    "currency",
    "received_stock_qty",
    "open_receipt_stock_qty",
]
_IDENTIFIER = Field(min_length=1, max_length=140)
_QUANTITY_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$"


def _tuple_from_json(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value


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
    frappe_revision: str | None = Field(default=None, max_length=140)
    erpnext_revision: str | None = Field(default=None, max_length=140)
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

    @field_validator(
        "authorized_warehouse", "source_modified_at", "frappe_revision", "erpnext_revision"
    )
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


CoachAnswerStatus = Literal["ANSWERED", "UNKNOWN", "CONFLICT", "REFUSED"]
CoachClaimType = Literal[
    "ERP_FACT",
    "RETRIEVED_KNOWLEDGE",
    "MEMORY",
    "RECOMMENDATION",
    "UNKNOWN",
]
CoachSignableClaimType = Literal["ERP_FACT", "RETRIEVED_KNOWLEDGE", "RECOMMENDATION"]
COACH_SIGNABLE_CLAIM_TYPES: frozenset[CoachSignableClaimType] = frozenset(
    {"ERP_FACT", "RETRIEVED_KNOWLEDGE", "RECOMMENDATION"}
)


class CoachLiveCitation(StrictModel):
    """A citation to one fact in the server-selected live ERP snapshot."""

    citation_type: Literal["LIVE_ERP"] = "LIVE_ERP"
    citation_id: str = Field(pattern=_ID_PATTERN, min_length=1, max_length=120)
    run_id: UUID
    document_doctype: CoachDocumentType
    document_name: str = _IDENTIFIER
    state_version: int = Field(ge=1, le=1_000_000)
    captured_at: str = _IDENTIFIER
    source_modified_at: str | None = Field(default=None, max_length=140)
    frappe_revision: str | None = Field(default=None, max_length=140)
    erpnext_revision: str | None = Field(default=None, max_length=140)
    fact_fields: Annotated[tuple[CoachLiveFactField, ...], Field(min_length=1, max_length=16)]
    fact_digest: str = Field(pattern=_DIGEST_PATTERN, min_length=64, max_length=64)

    @field_validator("run_id", mode="before")
    @classmethod
    def validate_run_uuid(cls, value: object) -> UUID:
        return _canonical_uuid(value, "run_id")

    @field_validator("document_name", "captured_at")
    @classmethod
    def validate_required_metadata(cls, value: str, info: object) -> str:
        return _nonblank(value, str(getattr(info, "field_name", "field")))

    @field_validator("source_modified_at", "frappe_revision", "erpnext_revision")
    @classmethod
    def validate_optional_source_time(cls, value: str | None) -> str | None:
        return None if value is None else _nonblank(value, "source_modified_at")

    @field_validator("fact_fields", mode="before")
    @classmethod
    def validate_fact_fields_tuple(cls, value: object) -> object:
        return _tuple_from_json(value)

    @model_validator(mode="after")
    def validate_unique_fact_fields(self) -> CoachLiveCitation:
        if len(set(self.fact_fields)) != len(self.fact_fields):
            raise ValueError("live citation fact fields must be unique")
        return self


class CoachProviderLiveCitation(StrictModel):
    """The minimal live citation a provider may emit.

    Snapshot identity and fact digests are server-owned metadata.  Requiring
    the model to copy those long values made the provider wire contract
    impossible to follow reliably; the service binds this selector to the
    authorized current snapshot before any answer can be displayed.
    """

    # The provider may copy server-owned citation metadata from its context.
    # Those fields are deliberately ignored here and are never used for
    # materialization; the final CoachLiveCitation remains strict.
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)

    citation_type: Literal["LIVE_ERP"] = "LIVE_ERP"
    citation_id: str = Field(pattern=_ID_PATTERN, min_length=1, max_length=120)
    fact_fields: Annotated[tuple[CoachLiveFactField, ...], Field(min_length=1, max_length=16)]

    @field_validator("fact_fields", mode="before")
    @classmethod
    def validate_fact_fields_tuple(cls, value: object) -> object:
        return _tuple_from_json(value)

    @model_validator(mode="after")
    def validate_unique_fact_fields(self) -> CoachProviderLiveCitation:
        if len(set(self.fact_fields)) != len(self.fact_fields):
            raise ValueError("live citation fact fields must be unique")
        return self


class CoachRetrievalCitation(StrictModel):
    """A citation to an exact, bounded T04 SearchHit."""

    citation_type: Literal["RETRIEVAL"] = "RETRIEVAL"
    citation_id: str = Field(pattern=_ID_PATTERN, min_length=1, max_length=120)
    chunk_id: str = Field(pattern=_DIGEST_PATTERN, min_length=64, max_length=64)
    content_digest: str = Field(pattern=_DIGEST_PATTERN, min_length=64, max_length=64)
    ordinal: int = Field(ge=1, le=1_000_000)
    source_type: str = _IDENTIFIER
    revision: str = _IDENTIFIER
    erp_version: str = _IDENTIFIER
    permission_scope: str = _IDENTIFIER


class CoachMemoryCitation(StrictModel):
    """The future Memory citation shape; T07 accepts it only after service resolution."""

    citation_type: Literal["MEMORY"] = "MEMORY"
    citation_id: str = Field(pattern=_ID_PATTERN, min_length=1, max_length=120)
    memory_id: UUID
    memory_version: int = Field(ge=1, le=1_000_000)
    content_digest: str = Field(pattern=_DIGEST_PATTERN, min_length=64, max_length=64)
    reviewed_at: str = _IDENTIFIER
    reviewer: str = _IDENTIFIER

    @field_validator("memory_id", mode="before")
    @classmethod
    def validate_memory_uuid(cls, value: object) -> UUID:
        return _canonical_uuid(value, "memory_id")


CoachCitation = Annotated[
    CoachLiveCitation | CoachRetrievalCitation | CoachMemoryCitation,
    Field(discriminator="citation_type"),
]
CoachProviderCitation = (
    CoachLiveCitation | CoachProviderLiveCitation | CoachRetrievalCitation | CoachMemoryCitation
)


class CoachCitationProvenance(StrictModel):
    """The exact citations that support one validated Coach claim."""

    citations: Annotated[tuple[CoachCitation, ...], Field(min_length=1, max_length=8)]

    @field_validator("citations", mode="before")
    @classmethod
    def validate_citations_tuple(cls, value: object) -> object:
        return _tuple_from_json(value)

    @model_validator(mode="after")
    def validate_unique_citations(self) -> CoachCitationProvenance:
        identifiers = [citation.citation_id for citation in self.citations]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("validated citation ids must be unique")
        return self


class CoachClaim(StrictModel):
    claim_id: str = Field(pattern=_ID_PATTERN, min_length=1, max_length=120)
    ordinal: int = Field(ge=1, le=32)
    claim_type: CoachClaimType
    text: str = Field(min_length=1, max_length=4_000)
    citation_refs: Annotated[tuple[str, ...], Field(min_length=1, max_length=8)] = ()

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _nonblank(value, "claim text")

    @field_validator("citation_refs", mode="before")
    @classmethod
    def validate_refs_tuple(cls, value: object) -> object:
        return _tuple_from_json(value)

    @model_validator(mode="after")
    def validate_unique_refs(self) -> CoachClaim:
        if len(set(self.citation_refs)) != len(self.citation_refs):
            raise ValueError("claim citation refs must be unique")
        return self


class CoachProviderOutput(StrictModel):
    """Strict model response before evidence resolution."""

    schema_version: Literal["1"] = "1"
    answer_status: CoachAnswerStatus
    answer: str = Field(default="", max_length=8_000)
    claims: Annotated[tuple[CoachClaim, ...], Field(max_length=32)] = ()
    citations: Annotated[tuple[CoachProviderCitation, ...], Field(max_length=64)] = ()
    refusal_reason: str | None = Field(default=None, max_length=500)

    @field_validator("claims", "citations", mode="before")
    @classmethod
    def validate_collection_tuple(cls, value: object) -> object:
        return _tuple_from_json(value)

    @field_validator("answer")
    @classmethod
    def validate_answer_text(cls, value: str) -> str:
        return value if not value or value.strip() else ""

    @field_validator("refusal_reason")
    @classmethod
    def validate_refusal_reason(cls, value: str | None) -> str | None:
        return None if value is None else _nonblank(value, "refusal_reason")

    @model_validator(mode="after")
    def validate_claim_graph(self) -> CoachProviderOutput:
        citation_ids = [citation.citation_id for citation in self.citations]
        if len(set(citation_ids)) != len(citation_ids):
            raise ValueError("citation ids must be unique")
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("claim ids must be unique")
        if tuple(claim.ordinal for claim in self.claims) != tuple(range(1, len(self.claims) + 1)):
            raise ValueError("claim ordinals must be contiguous")
        citation_map = {citation.citation_id: citation for citation in self.citations}
        referenced: set[str] = set()
        for claim in self.claims:
            for reference in claim.citation_refs:
                citation = citation_map.get(reference)
                if citation is None:
                    raise ValueError("claim citation ref is not supplied")
                referenced.add(reference)
                if claim.claim_type == "ERP_FACT" and citation.citation_type != "LIVE_ERP":
                    raise ValueError("ERP_FACT claims require LIVE_ERP citations")
                if (
                    claim.claim_type == "RETRIEVED_KNOWLEDGE"
                    and citation.citation_type != "RETRIEVAL"
                ):
                    raise ValueError("RETRIEVED_KNOWLEDGE claims require RETRIEVAL citations")
        if referenced != set(citation_ids):
            raise ValueError("orphan citations are not permitted")
        if self.answer_status in {"ANSWERED", "CONFLICT"} and any(
            claim.claim_type == "UNKNOWN" for claim in self.claims
        ):
            raise ValueError("displayable answers cannot contain UNKNOWN claims")
        if self.answer_status == "ANSWERED":
            if not self.answer.strip() or not self.claims:
                raise ValueError("ANSWERED requires an answer and claims")
            if self.refusal_reason is not None:
                raise ValueError("ANSWERED cannot include refusal_reason")
        elif self.answer_status in {"UNKNOWN", "REFUSED"}:
            if self.answer.strip() or self.claims or self.citations or not self.refusal_reason:
                raise ValueError("UNKNOWN and REFUSED require a reason and no claims")
        elif self.answer_status == "CONFLICT":
            if not self.answer.strip() or not self.claims or not self.citations:
                raise ValueError("CONFLICT requires cited claims")
            if self.refusal_reason is not None:
                raise ValueError("CONFLICT cannot include refusal_reason")
        return self


class CoachTokenUsage(StrictModel):
    prompt_tokens: int = Field(default=0, ge=0, le=10_000_000)
    completion_tokens: int = Field(default=0, ge=0, le=10_000_000)
    reasoning_tokens: int = Field(default=0, ge=0, le=10_000_000)


class CoachRetrievalTrace(StrictModel):
    selected_chunk_ids: Annotated[tuple[str, ...], Field(max_length=5)] = ()
    selected_content_digests: Annotated[tuple[str, ...], Field(max_length=5)] = ()
    selected_revisions: Annotated[tuple[str, ...], Field(max_length=5)] = ()
    live_fact_digests: Annotated[tuple[str, ...], Field(max_length=50)] = ()
    provider_tools: Annotated[tuple[str, ...], Field(max_length=0)] = ()
    context_fragment_ids: Annotated[tuple[str, ...], Field(max_length=64)] = ()

    @field_validator(
        "selected_chunk_ids",
        "selected_content_digests",
        "selected_revisions",
        "live_fact_digests",
        "provider_tools",
        "context_fragment_ids",
        mode="before",
    )
    @classmethod
    def validate_trace_tuples(cls, value: object) -> object:
        return _tuple_from_json(value)

    @field_validator("selected_chunk_ids", "selected_content_digests", "live_fact_digests")
    @classmethod
    def validate_trace_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for digest in value:
            if not re.fullmatch(_DIGEST_PATTERN, digest):
                raise ValueError("trace digest is invalid")
        return value


class ValidatedCoachClaim(StrictModel):
    """Runtime-validated, signed payload accepted by the Frappe authority.

    The signature is intentionally part of the wire contract.  Frappe does
    not trust a caller-provided boolean or an internal-looking field; it
    verifies the domain-separated HMAC over every field before persistence.
    """

    schema_version: Literal["1"] = "1"
    run_id: UUID
    correlation_id: UUID
    claim_id: str = Field(pattern=_ID_PATTERN, min_length=1, max_length=120)
    ordinal: int = Field(ge=1, le=32)
    claim_type: CoachSignableClaimType
    claim_text: str = Field(min_length=1, max_length=4_000)
    claim_digest: str = Field(pattern=_DIGEST_PATTERN, min_length=64, max_length=64)
    citation_provenance: CoachCitationProvenance
    citation_digest: str = Field(pattern=_DIGEST_PATTERN, min_length=64, max_length=64)
    source_revision: str = Field(min_length=1, max_length=140)
    source_snapshot: str = Field(min_length=2, max_length=16_000)
    signature: str = Field(pattern=_DIGEST_PATTERN, min_length=64, max_length=64)

    @field_validator("run_id", "correlation_id", mode="before")
    @classmethod
    def validate_identity_uuid(cls, value: object, info: object) -> UUID:
        field_name = getattr(info, "field_name", "identity")
        return _canonical_uuid(value, str(field_name))

    @field_validator("claim_text", "source_revision")
    @classmethod
    def validate_package_text(cls, value: str, info: object) -> str:
        return _nonblank(value, str(getattr(info, "field_name", "text")))

    @field_validator("source_snapshot")
    @classmethod
    def validate_snapshot_json(cls, value: str) -> str:
        try:
            decoded = json.loads(value)
            if not isinstance(decoded, dict) or canonical_json(decoded) != value:
                raise ValueError("source snapshot must be canonical JSON")
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("source snapshot must be canonical JSON") from error
        return value

    @model_validator(mode="after")
    def validate_package_digests(self) -> ValidatedCoachClaim:
        expected_claim = hashlib.sha256(self.claim_text.encode("utf-8")).hexdigest()
        if self.claim_digest != expected_claim:
            raise ValueError("claim digest does not match claim text")
        provenance = canonical_json(self.citation_provenance.model_dump(mode="json"))
        expected_citations = hashlib.sha256(provenance.encode("utf-8")).hexdigest()
        if self.citation_digest != expected_citations:
            raise ValueError("citation digest does not match provenance")
        return self


class CoachAnswer(CoachProviderOutput):
    """Validated answer plus bounded, non-secret execution metadata."""

    citations: Annotated[tuple[CoachCitation, ...], Field(max_length=64)] = ()

    retrieval_trace: CoachRetrievalTrace
    token_usage: CoachTokenUsage = CoachTokenUsage()
    latency_ms: int = Field(ge=0, le=86_400_000)
    validated_claims: Annotated[tuple[ValidatedCoachClaim, ...], Field(max_length=32)] = ()

    @field_validator("validated_claims", mode="before")
    @classmethod
    def validate_claims_tuple(cls, value: object) -> object:
        return _tuple_from_json(value)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_coach_provider_output(raw: object) -> CoachProviderOutput:
    """Parse provider JSON with bounded, explicit wire compatibility.

    Some OpenAI-compatible models render the current contract version as the
    semantically equivalent string ``"1.0"`` even when the prompt requests
    ``"1"``.  Normalize only that exact string before strict validation; all
    other versions and numeric values remain rejected.
    """
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 256_000:
        raise ValueError("provider Coach output is invalid")
    import json

    value = json.loads(
        raw,
        parse_constant=_reject_json_constant,
        object_pairs_hook=_reject_duplicate_pairs,
    )
    if not isinstance(value, dict):
        raise ValueError("provider Coach output must be an object")
    if value.get("schema_version") == "1.0":
        value = {**value, "schema_version": "1"}
    return CoachProviderOutput.model_validate(value)


__all__ = [
    "CoachAnswer",
    "CoachAnswerStatus",
    "CoachCitation",
    "CoachCitationProvenance",
    "CoachClaim",
    "CoachClaimType",
    "CoachCoverage",
    "CoachCurrentDocumentContext",
    "CoachDocumentRef",
    "CoachDocumentType",
    "CoachLiveCitation",
    "CoachLiveFactField",
    "CoachMemoryCitation",
    "CoachProviderOutput",
    "CoachQuestionRequest",
    "CoachRetrievalCitation",
    "CoachRetrievalTrace",
    "CoachTokenUsage",
    "MaterialRequestCurrentFact",
    "PurchaseOrderCurrentFact",
    "ValidatedCoachClaim",
    "parse_coach_provider_output",
]
