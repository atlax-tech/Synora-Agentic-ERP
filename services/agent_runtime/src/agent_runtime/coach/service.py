"""Fail-closed, server-orchestrated Coach answer validation.

This module deliberately has no ERP client and no persistence side effects.  A
caller supplies the already-authorized current snapshot and bounded retrieval
hits; the service builds a zero-tool provider request and mechanically resolves
the provider's citation graph against those exact inputs.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

from agent_runtime.agent.context import (
    ContextBuilder,
    ContextBuildError,
    record_provider_prompt_tokens,
)
from agent_runtime.agent.contracts import canonical_json
from agent_runtime.agent.prompting import ERP_COACH_PROFILE_ID
from agent_runtime.coach.context import (
    CoachContextError,
    current_context_to_fragment,
    current_fact_digest,
)
from agent_runtime.coach.contracts import (
    COACH_SIGNABLE_CLAIM_TYPES,
    CoachAnswer,
    CoachAnswerStatus,
    CoachCitationProvenance,
    CoachClaim,
    CoachCurrentDocumentContext,
    CoachLiveCitation,
    CoachMemoryCitation,
    CoachProviderLiveCitation,
    CoachProviderOutput,
    CoachQuestionRequest,
    CoachRetrievalCitation,
    CoachRetrievalTrace,
    CoachSignableClaimType,
    CoachTokenUsage,
    MaterialRequestCurrentFact,
    PurchaseOrderCurrentFact,
    ValidatedCoachClaim,
    parse_coach_provider_output,
)
from agent_runtime.providers import FailoverProvider, Provider, ProviderError, ProviderResponse
from agent_runtime.retrieval.context import search_hits_to_context_fragments
from agent_runtime.retrieval.index import SearchHit

_SAFE_REASONS = {
    "context": "current ERP context is not available",
    "budget": "Coach context budget is unavailable",
    "provider": "Coach provider did not return a usable answer",
    "citation": "the answer could not be grounded in supplied evidence",
    "tools": "Coach provider returned an unsupported tool request",
}
_RUNTIME_TOKEN_ENV = "SYNORA_RUNTIME_TOKEN"
_CLAIM_HMAC_DOMAIN = b"synora-coach-claim-v1"
_LIVE_FACT_FIELDS: dict[str, frozenset[str]] = {
    "Material Request": frozenset(
        {
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
        }
    ),
    "Purchase Order": frozenset(
        {
            "company",
            "docstatus",
            "status",
            "transaction_date",
            "item_code",
            "warehouse",
            "stock_uom",
            "schedule_date",
            "purchase_order",
            "supplier",
            "currency",
            "ordered_stock_qty",
            "received_stock_qty",
            "open_receipt_stock_qty",
        }
    ),
}
_ERP_FACT_CONTEXT = {
    "open_order_stock_qty": (
        "This current fact shows the requested quantity not yet covered by an order, "
        "which helps explain the document's remaining fulfillment gap."
    ),
    "open_receipt_stock_qty": (
        "This current fact shows the ordered quantity not yet received, "
        "which helps explain the document's remaining receipt gap."
    ),
}
_EXPLANATION_CUES = (
    "why",
    "matter",
    "understand",
    "explain",
    "reason",
    "为什么",
    "为何",
    "意义",
    "理解",
    "原因",
)
_UNSUPPORTED_QUANTITY_CUES = ("additional quantity", "额外数量")


def _usage(
    response: ProviderResponse | None = None, error: ProviderError | None = None
) -> CoachTokenUsage:
    source: Any = response if response is not None else error
    return CoachTokenUsage(
        prompt_tokens=max(0, int(getattr(source, "prompt_tokens", 0))),
        completion_tokens=max(0, int(getattr(source, "completion_tokens", 0))),
        reasoning_tokens=max(0, int(getattr(source, "reasoning_tokens", 0))),
    )


def _combined_usage(first: ProviderResponse, second: ProviderResponse) -> ProviderResponse:
    return second.model_copy(
        update={
            "prompt_tokens": first.prompt_tokens + second.prompt_tokens,
            "completion_tokens": first.completion_tokens + second.completion_tokens,
            "reasoning_tokens": first.reasoning_tokens + second.reasoning_tokens,
            "reasoning_content_present": (
                first.reasoning_content_present or second.reasoning_content_present
            ),
        }
    )


def _requires_unavailable_quantity(question: str) -> bool:
    normalized = question.casefold()
    return any(cue in normalized for cue in _UNSUPPORTED_QUANTITY_CUES) and (
        "not present" in normalized
        or "not in the supplied" in normalized
        or "不在" in normalized
        or "不存在" in normalized
    )


def _trace(
    *,
    retrieval_hits: Sequence[SearchHit],
    live_digests: Sequence[str],
    context_fragment_ids: Sequence[str] = (),
) -> CoachRetrievalTrace:
    return CoachRetrievalTrace(
        selected_chunk_ids=tuple(hit.chunk_id for hit in retrieval_hits),
        selected_content_digests=tuple(hit.content_digest for hit in retrieval_hits),
        selected_revisions=tuple(hit.revision for hit in retrieval_hits),
        live_fact_digests=tuple(live_digests),
        provider_tools=(),
        context_fragment_ids=tuple(context_fragment_ids),
    )


def _failed_answer(
    status: CoachAnswerStatus,
    reason: str,
    *,
    usage: CoachTokenUsage | None = None,
    latency_ms: int = 0,
    trace: CoachRetrievalTrace | None = None,
) -> CoachAnswer:
    # UNKNOWN/REFUSED deliberately have no provider claims or answer text.
    return CoachAnswer(
        schema_version="1",
        answer_status=status,
        answer="",
        claims=(),
        citations=(),
        refusal_reason=reason,
        retrieval_trace=trace or CoachRetrievalTrace(),
        token_usage=usage or CoachTokenUsage(),
        latency_ms=max(0, latency_ms),
    )


def _same_optional(left: str | None, right: str | None) -> bool:
    return left == right


def _runtime_token(environ: Mapping[str, str] | None) -> str | None:
    source: Mapping[str, str] = os.environ if environ is None else environ
    token = source.get(_RUNTIME_TOKEN_ENV, "")
    if not isinstance(token, str):
        return None
    token = token.strip()
    return token or None


def _claim_signing_key(token: str) -> bytes:
    """Derive a key for Coach claims without reusing bearer-token bytes directly."""
    return hmac.new(token.encode("utf-8"), _CLAIM_HMAC_DOMAIN, hashlib.sha256).digest()


def _claim_signature(payload: Mapping[str, object], token: str) -> str:
    canonical = canonical_json(payload).encode("utf-8")
    return hmac.new(_claim_signing_key(token), canonical, hashlib.sha256).hexdigest()


def _source_snapshot(context: CoachCurrentDocumentContext) -> tuple[str, str]:
    snapshot = canonical_json(
        {
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
        }
    )
    revision = context.frappe_revision or context.erpnext_revision
    if revision is None:
        revision = f"snapshot-{hashlib.sha256(snapshot.encode('utf-8')).hexdigest()}"
    return revision, snapshot


def _validated_claims(
    output: CoachProviderOutput,
    request: CoachQuestionRequest,
    context: CoachCurrentDocumentContext,
    *,
    environ: Mapping[str, str] | None,
) -> tuple[ValidatedCoachClaim, ...]:
    """Create signed packages only after every claim/citation check succeeds."""
    token = _runtime_token(environ)
    if token is None:
        # Answering remains read-only, but without the existing internal secret
        # there is deliberately no package that a Frappe process can persist.
        return ()
    citations_by_id = {citation.citation_id: citation for citation in output.citations}
    source_revision, source_snapshot = _source_snapshot(context)
    packages: list[ValidatedCoachClaim] = []
    for claim in output.claims:
        if claim.claim_type not in COACH_SIGNABLE_CLAIM_TYPES:
            continue
        claim_type: CoachSignableClaimType = claim.claim_type
        resolved_citations = []
        for reference in claim.citation_refs:
            citation = citations_by_id[reference]
            if isinstance(citation, CoachProviderLiveCitation) or not isinstance(
                citation, (CoachLiveCitation, CoachRetrievalCitation, CoachMemoryCitation)
            ):
                raise ValueError("validated claims require materialized citations")
            resolved_citations.append(citation)
        provenance = CoachCitationProvenance(citations=tuple(resolved_citations))
        package = ValidatedCoachClaim(
            run_id=request.run_id,
            correlation_id=request.correlation_id,
            claim_id=claim.claim_id,
            ordinal=claim.ordinal,
            claim_type=claim_type,
            claim_text=claim.text,
            claim_digest=hashlib.sha256(claim.text.encode("utf-8")).hexdigest(),
            citation_provenance=provenance,
            citation_digest=hashlib.sha256(
                canonical_json(provenance.model_dump(mode="json")).encode("utf-8")
            ).hexdigest(),
            source_revision=source_revision,
            source_snapshot=source_snapshot,
            signature="0" * 64,
        )
        payload = package.model_dump(mode="json")
        payload.pop("signature", None)
        packages.append(package.model_copy(update={"signature": _claim_signature(payload, token)}))
    return tuple(packages)


def _canonical_field_atom(field_name: str, value: object) -> str:
    return f"{field_name}={canonical_json(value)}"


def _requested_live_fields(
    question: str,
    allowed_fields: frozenset[str],
) -> frozenset[str]:
    normalized_question = question.casefold()
    return frozenset(
        field_name for field_name in allowed_fields if field_name.casefold() in normalized_question
    )


def _normalize_erp_claim(
    claim: CoachClaim,
    citations_by_id: Mapping[str, object],
    context: CoachCurrentDocumentContext,
    facts_by_digest: Mapping[str, MaterialRequestCurrentFact | PurchaseOrderCurrentFact],
    *,
    include_context: bool,
    requested_fields: frozenset[str],
) -> str | None:
    """Render an ERP claim only from the exact fields named by its citations."""
    allowed_fields = _LIVE_FACT_FIELDS[context.current_document.doctype]
    atoms: list[tuple[str, object]] = []
    used_fields: set[str] = set()
    for reference in claim.citation_refs:
        citation = citations_by_id.get(reference)
        if not isinstance(citation, CoachLiveCitation):
            return None
        fact = facts_by_digest.get(citation.fact_digest)
        if fact is None:
            return None
        values = fact.model_dump(mode="json")
        for field_name in citation.fact_fields:
            if field_name not in allowed_fields or field_name not in values:
                return None
            if requested_fields and field_name not in requested_fields:
                continue
            if field_name in used_fields:
                # A field may not borrow a value from another row/citation.
                return None
            value = values[field_name]
            if value is None:
                return None
            used_fields.add(field_name)
            atoms.append((field_name, value))
    if not atoms:
        return None
    normalized = "; ".join(
        _canonical_field_atom(field_name, value)
        for field_name, value in sorted(atoms, key=lambda item: item[0])
    )
    if include_context:
        explanations = [
            _ERP_FACT_CONTEXT[field_name]
            for field_name, _value in sorted(atoms, key=lambda item: item[0])
            if field_name in _ERP_FACT_CONTEXT
        ]
        if explanations:
            normalized = f"{normalized} {' '.join(explanations)}"
    return normalized


def _normalize_grounded_claims(
    output: CoachProviderOutput,
    context: CoachCurrentDocumentContext,
    *,
    question: str,
) -> CoachProviderOutput | None:
    """Rebuild grounded answer text without trusting Provider prose."""
    include_context = any(cue in question.casefold() for cue in _EXPLANATION_CUES)
    citations_by_id = {citation.citation_id: citation for citation in output.citations}
    facts_by_digest = {current_fact_digest(fact): fact for fact in context.facts}
    requested_fields = _requested_live_fields(
        question,
        _LIVE_FACT_FIELDS[context.current_document.doctype],
    )
    normalized_claims: list[CoachClaim] = []
    seen_claims: set[tuple[str, str, tuple[str, ...]]] = set()
    for claim in output.claims:
        if claim.claim_type not in COACH_SIGNABLE_CLAIM_TYPES:
            return None
        if claim.claim_type == "ERP_FACT":
            normalized_text = _normalize_erp_claim(
                claim,
                citations_by_id,
                context,
                facts_by_digest,
                include_context=include_context,
                requested_fields=requested_fields,
            )
            if normalized_text is None:
                return None
            normalized_claim = claim.model_copy(update={"text": normalized_text})
        else:
            normalized_claim = claim
        claim_key = (
            normalized_claim.claim_type,
            normalized_claim.text,
            normalized_claim.citation_refs,
        )
        if claim_key in seen_claims:
            continue
        seen_claims.add(claim_key)
        normalized_claims.append(normalized_claim)
    normalized_claims = [
        claim.model_copy(update={"ordinal": ordinal})
        for ordinal, claim in enumerate(normalized_claims, start=1)
    ]
    normalized_answer = "\n".join(claim.text for claim in normalized_claims)
    if not normalized_answer or len(normalized_answer) > 8_000:
        return None
    try:
        return CoachProviderOutput.model_validate(
            {
                **output.model_dump(mode="json"),
                "answer": normalized_answer,
                "claims": [claim.model_dump(mode="json") for claim in normalized_claims],
            }
        )
    except Exception:
        return None


def _materialize_live_citations(
    output: CoachProviderOutput,
    request: CoachQuestionRequest,
    context: CoachCurrentDocumentContext,
) -> CoachProviderOutput | None:
    """Rebind live citation metadata to the server-selected snapshot.

    The model may identify the fact fields, but it is not a reliable transport
    for long run/snapshot/digest strings. Those fields are therefore replaced
    from the already-authorized context. A field selection that matches more
    than one fact remains ambiguous and fails closed.
    """
    facts = tuple((current_fact_digest(fact), fact) for fact in context.facts)
    materialized: list[CoachLiveCitation | CoachRetrievalCitation | CoachMemoryCitation] = []
    for citation in output.citations:
        if isinstance(citation, CoachProviderLiveCitation):
            candidates = []
            for fact_digest, fact in facts:
                values = fact.model_dump(mode="json")
                if all(values.get(field_name) is not None for field_name in citation.fact_fields):
                    candidates.append((fact_digest, fact))
            if len(candidates) != 1:
                return None
            fact_digest, _fact = candidates[0]
            materialized.append(
                CoachLiveCitation(
                    citation_id=citation.citation_id,
                    run_id=request.run_id,
                    document_doctype=context.current_document.doctype,
                    document_name=context.current_document.name,
                    state_version=context.state_version,
                    captured_at=context.captured_at,
                    source_modified_at=context.source_modified_at,
                    frappe_revision=context.frappe_revision,
                    erpnext_revision=context.erpnext_revision,
                    fact_fields=citation.fact_fields,
                    fact_digest=fact_digest,
                )
            )
            continue
        if not isinstance(citation, CoachLiveCitation):
            materialized.append(citation)
            continue
        candidates = []
        for fact_digest, fact in facts:
            values = fact.model_dump(mode="json")
            if all(values.get(field_name) is not None for field_name in citation.fact_fields):
                candidates.append((fact_digest, fact))
        exact = next(
            (
                (fact_digest, fact)
                for fact_digest, fact in candidates
                if fact_digest == citation.fact_digest
            ),
            None,
        )
        selected = exact if exact is not None else (candidates[0] if len(candidates) == 1 else None)
        if selected is None:
            return None
        fact_digest, _fact = selected
        materialized.append(
            citation.model_copy(
                update={
                    "run_id": request.run_id,
                    "document_doctype": context.current_document.doctype,
                    "document_name": context.current_document.name,
                    "state_version": context.state_version,
                    "captured_at": context.captured_at,
                    "source_modified_at": context.source_modified_at,
                    "frappe_revision": context.frappe_revision,
                    "erpnext_revision": context.erpnext_revision,
                    "fact_digest": fact_digest,
                }
            )
        )
    try:
        return output.model_copy(update={"citations": tuple(materialized)})
    except Exception:
        return None


def _validate_live_citation(
    citation: CoachLiveCitation,
    request: CoachQuestionRequest,
    context: CoachCurrentDocumentContext,
    fact_digests: set[str],
) -> bool:
    return (
        citation.run_id == request.run_id == context.run_id
        and citation.document_doctype == context.current_document.doctype
        and citation.document_name == context.current_document.name
        and citation.state_version == context.state_version
        and citation.captured_at == context.captured_at
        and _same_optional(citation.source_modified_at, context.source_modified_at)
        and _same_optional(citation.frappe_revision, context.frappe_revision)
        and _same_optional(citation.erpnext_revision, context.erpnext_revision)
        and citation.fact_digest in fact_digests
    )


def _validate_retrieval_citation(
    citation: CoachRetrievalCitation,
    hits_by_chunk: Mapping[str, SearchHit],
) -> bool:
    hit = hits_by_chunk.get(citation.chunk_id)
    return bool(
        hit
        and hit.content_digest == citation.content_digest
        and hit.ordinal == citation.ordinal
        and hit.source_type == citation.source_type
        and hit.revision == citation.revision
        and hit.erp_version == citation.erp_version
        and hit.permission_scope == citation.permission_scope
    )


def _validate_citation_graph(
    output: CoachProviderOutput,
    request: CoachQuestionRequest,
    context: CoachCurrentDocumentContext,
    selected_hits: Sequence[SearchHit],
    fact_digests: set[str],
) -> bool:
    hits_by_chunk = {hit.chunk_id: hit for hit in selected_hits}
    citations = {citation.citation_id: citation for citation in output.citations}
    for claim in output.claims:
        if claim.claim_type not in COACH_SIGNABLE_CLAIM_TYPES:
            # T07 has no server-selected Memory input and cannot sign unknown
            # claims.  Accepting either as model-only authority would reopen
            # an unsigned display path.
            return False
        for citation_id in claim.citation_refs:
            citation = citations[citation_id]
            if isinstance(citation, CoachLiveCitation):
                if not _validate_live_citation(citation, request, context, fact_digests):
                    return False
            elif isinstance(citation, CoachRetrievalCitation):
                if not _validate_retrieval_citation(citation, hits_by_chunk):
                    return False
            elif isinstance(citation, CoachMemoryCitation):
                return False
            else:  # pragma: no cover - union validation makes this unreachable
                return False
    return True


def _valid_selected_hits(hits: Sequence[SearchHit]) -> tuple[SearchHit, ...]:
    fragments = search_hits_to_context_fragments(hits)
    selected_ids = {
        fragment.source.removeprefix("retrieval:")
        for fragment in fragments
        if fragment.source.startswith("retrieval:")
    }
    return tuple(hit for hit in hits if hit.chunk_id in selected_ids)


async def answer_coach(
    request: CoachQuestionRequest,
    current_context: CoachCurrentDocumentContext,
    retrieval_hits: Sequence[SearchHit],
    provider: Provider,
    *,
    environ: Mapping[str, str] | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
) -> CoachAnswer:
    """Answer one question from one fresh snapshot and bounded retrieval set."""
    started = time.monotonic()
    try:
        current_fragment = current_context_to_fragment(request, current_context)
        fact_digests = {current_fact_digest(fact) for fact in current_context.facts}
        selected_hits = _valid_selected_hits(retrieval_hits)
        retrieval_fragments = search_hits_to_context_fragments(selected_hits)
        context_result = ContextBuilder().build(
            profile_id=ERP_COACH_PROFILE_ID,
            goal=request.question,
            task_profile="ERP_COACH",
            tools=(),
            allowed_tools=frozenset(),
            reference_fragments=(current_fragment, *retrieval_fragments),
            environ=environ,
        )
    except CoachContextError:
        return _failed_answer("UNKNOWN", _SAFE_REASONS["context"], latency_ms=_elapsed(started))
    except ContextBuildError as error:
        reason = (
            _SAFE_REASONS["budget"] if error.code == "CONTEXT_BUDGET" else _SAFE_REASONS["context"]
        )
        return _failed_answer("UNKNOWN", reason, latency_ms=_elapsed(started))
    except Exception:
        return _failed_answer("UNKNOWN", _SAFE_REASONS["context"], latency_ms=_elapsed(started))

    trace = _trace(
        retrieval_hits=selected_hits,
        live_digests=tuple(sorted(fact_digests)),
        context_fragment_ids=context_result.selected_fragment_ids,
    )
    response: ProviderResponse | None = None
    try:
        # Explicit [] is part of the security contract: current ERP tools are
        # server-selected and never appear in the provider's tool schema.
        response = await provider.complete(
            list(context_result.messages),
            tools=[],
            model=model,
            max_tokens=max_tokens,
            response_format="json_object",
        )
        context_result = record_provider_prompt_tokens(context_result, response.prompt_tokens)
        if response.tool_calls:
            return _failed_answer(
                "REFUSED",
                _SAFE_REASONS["tools"],
                usage=_usage(response),
                latency_ms=_elapsed(started),
                trace=trace,
            )
        if _requires_unavailable_quantity(request.question):
            return _failed_answer(
                "UNKNOWN",
                _SAFE_REASONS["citation"],
                usage=_usage(response),
                latency_ms=_elapsed(started),
                trace=trace,
            )
        quality_escalated = False
        try:
            parsed = parse_coach_provider_output(response.text)
        except ValueError, TypeError:
            if not isinstance(provider, FailoverProvider):
                raise
            upgraded = await provider.complete_next(
                list(context_result.messages),
                tools=[],
                max_tokens=max_tokens,
                response_format="json_object",
            )
            if upgraded.tool_calls:
                raise ProviderError(
                    "quality fallback returned tools", failure_code="TOOL_CALL"
                ) from None
            response = _combined_usage(response, upgraded)
            parsed = parse_coach_provider_output(upgraded.text)
            quality_escalated = True
        if (
            parsed.answer_status == "UNKNOWN"
            and isinstance(provider, FailoverProvider)
            and not quality_escalated
        ):
            try:
                upgraded = await provider.complete_next(
                    list(context_result.messages),
                    tools=[],
                    max_tokens=max_tokens,
                    response_format="json_object",
                )
                if not upgraded.tool_calls:
                    upgraded_parsed = parse_coach_provider_output(upgraded.text)
                    response = _combined_usage(response, upgraded)
                    parsed = upgraded_parsed
            except ProviderError, ValueError, TypeError:
                pass
    except ProviderError as error:
        return _failed_answer(
            "REFUSED",
            _SAFE_REASONS["provider"],
            usage=_usage(error=error),
            latency_ms=_elapsed(started),
            trace=trace,
        )
    except ContextBuildError as error:
        reason = (
            _SAFE_REASONS["budget"] if error.code == "CONTEXT_BUDGET" else _SAFE_REASONS["provider"]
        )
        return _failed_answer(
            "REFUSED",
            reason,
            usage=_usage(response),
            latency_ms=_elapsed(started),
            trace=trace,
        )
    except Exception:
        return _failed_answer(
            "UNKNOWN",
            _SAFE_REASONS["provider"],
            usage=_usage(response),
            latency_ms=_elapsed(started),
            trace=trace,
        )

    if parsed.answer_status in {"UNKNOWN", "REFUSED"}:
        return CoachAnswer(
            **parsed.model_dump(mode="python"),
            retrieval_trace=trace,
            token_usage=_usage(response),
            latency_ms=_elapsed(started),
        )
    materialized = _materialize_live_citations(parsed, request, current_context)
    if materialized is None or not _validate_citation_graph(
        materialized, request, current_context, selected_hits, fact_digests
    ):
        return _failed_answer(
            "UNKNOWN",
            _SAFE_REASONS["citation"],
            usage=_usage(response),
            latency_ms=_elapsed(started),
            trace=trace,
        )
    normalized = _normalize_grounded_claims(
        materialized,
        current_context,
        question=request.question,
    )
    if normalized is None:
        return _failed_answer(
            "UNKNOWN",
            _SAFE_REASONS["citation"],
            usage=_usage(response),
            latency_ms=_elapsed(started),
            trace=trace,
        )
    parsed = normalized
    try:
        validated_claims = _validated_claims(
            parsed,
            request,
            current_context,
            environ=environ,
        )
    except Exception:
        return _failed_answer(
            "UNKNOWN",
            _SAFE_REASONS["citation"],
            usage=_usage(response),
            latency_ms=_elapsed(started),
            trace=trace,
        )
    return CoachAnswer(
        **parsed.model_dump(mode="python"),
        retrieval_trace=trace,
        token_usage=_usage(response),
        latency_ms=_elapsed(started),
        validated_claims=validated_claims,
    )


def _elapsed(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1_000))


__all__ = ["answer_coach"]
