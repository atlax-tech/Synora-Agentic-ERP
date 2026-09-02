"""Internal Runtime transport for the server-orchestrated ERP Coach.

The transport accepts only the identity, question, current document reference,
and the existing capability.  It chooses the read-only Gateway call and the
curated retrieval query locally; callers cannot provide ERP facts, retrieval
hits, provider configuration, or tool schemas.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Mapping

from pydantic import Field, SecretStr, field_validator

from agent_runtime.agent.execution import GatewayClientLike
from agent_runtime.coach.context import CoachContextError, build_current_document_context
from agent_runtime.coach.contracts import (
    CoachAnswer,
    CoachAnswerStatus,
    CoachQuestionRequest,
    CoachRetrievalTrace,
    CoachTokenUsage,
)
from agent_runtime.coach.service import answer_coach
from agent_runtime.gateway import (
    CurrentMaterialRequestCall,
    CurrentMaterialRequestInput,
    CurrentPurchaseOrderCall,
    CurrentPurchaseOrderInput,
    GatewayClient,
    GatewayClientError,
    GatewayRequest,
    ToolCall,
)
from agent_runtime.providers import (
    Provider,
    ProviderError,
    provider_from_environment,
    provider_max_output_tokens,
    provider_model,
)
from agent_runtime.retrieval.index import RetrievalIndex, SearchHit
from agent_runtime.retrieval.sources import ERP_VERSION, load_curated_sources


class CoachRuntimeRequest(CoachQuestionRequest):
    """Strict Frappe-to-Runtime Coach request.

    The inherited Coach question contract keeps the document and question
    shape identical to the service contract.  ``capability`` is the only
    transport-only field and remains a secret throughout the request.
    """

    capability: SecretStr = Field(repr=False)

    @field_validator("capability")
    @classmethod
    def validate_capability(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if len(token) != 43 or any(
            not (character.isascii() and (character.isalnum() or character in "_-"))
            for character in token
        ):
            raise ValueError("capability is invalid")
        return value


_SAFE_GATEWAY_REASON = "current ERP context is not available"
_SAFE_PROVIDER_REASON = "Coach provider is not available"
_MAX_RETRIEVAL_HITS = 5


def _coach_request(request: CoachRuntimeRequest) -> CoachQuestionRequest:
    """Drop the transport secret before entering the provider-neutral service."""
    return CoachQuestionRequest.model_validate(request.model_dump(exclude={"capability"}))


def _gateway_request(request: CoachRuntimeRequest) -> GatewayRequest:
    """Build the sole server-selected current-document read call."""
    tool: ToolCall
    if request.current_document.doctype == "Material Request":
        tool = CurrentMaterialRequestCall(
            name="material_request.current",
            input=CurrentMaterialRequestInput(name=request.current_document.name),
        )
    else:
        tool = CurrentPurchaseOrderCall(
            name="purchase_order.current",
            input=CurrentPurchaseOrderInput(name=request.current_document.name),
        )
    return GatewayRequest(
        run_id=request.run_id,
        capability=request.capability,
        correlation_id=request.correlation_id,
        tool=tool,
    )


def _retrieve_curated_sources(question: str) -> tuple[SearchHit, ...]:
    """Build a disposable, server-owned FTS5 index and return bounded hits."""
    with RetrievalIndex(":memory:") as index:
        index.ingest(load_curated_sources())
        return tuple(
            index.search(
                question,
                limit=_MAX_RETRIEVAL_HITS,
                permission_scope="internal",
                erp_version=ERP_VERSION,
            )
        )


def _failed_answer(
    status: CoachAnswerStatus,
    reason: str,
    *,
    latency_ms: int = 0,
) -> CoachAnswer:
    """Return a bounded response without exposing transport/provider errors."""
    return CoachAnswer(
        schema_version="1",
        answer_status=status,
        answer="",
        claims=(),
        citations=(),
        refusal_reason=reason,
        retrieval_trace=CoachRetrievalTrace(),
        token_usage=CoachTokenUsage(),
        latency_ms=max(0, latency_ms),
    )


async def _close_resource(resource: object | None) -> None:
    close = getattr(resource, "aclose", None)
    if close is None:
        return
    with contextlib.suppress(Exception):
        await close()


async def answer_coach_runtime(
    request: CoachRuntimeRequest,
    *,
    environ: Mapping[str, str] | None = None,
) -> CoachAnswer:
    """Answer one authenticated internal Coach request from live ERP facts."""
    started = time.monotonic()
    client: GatewayClientLike | None = None
    provider: Provider | None = None
    try:
        try:
            client = GatewayClient()
            gateway_response = await client.execute(_gateway_request(request))
            coach_request = _coach_request(request)
            current_context = build_current_document_context(coach_request, gateway_response)
        except GatewayClientError, CoachContextError, ValueError, TypeError:
            return _failed_answer("UNKNOWN", _SAFE_GATEWAY_REASON, latency_ms=_elapsed(started))

        try:
            retrieval_hits = _retrieve_curated_sources(coach_request.question)
        except Exception:
            # Live ERP evidence can still support an answer when the disposable
            # retrieval index is unavailable; the Coach service will see zero hits.
            retrieval_hits = ()

        try:
            # Resolve every provider setting from the same immutable snapshot
            # used for model and context-budget decisions.
            provider = provider_from_environment(environ=environ)
            max_output_tokens = provider_max_output_tokens(environ)
        except ProviderError, ValueError:
            return _failed_answer("REFUSED", _SAFE_PROVIDER_REASON, latency_ms=_elapsed(started))

        return await answer_coach(
            coach_request,
            current_context,
            retrieval_hits,
            provider,
            environ=environ,
            model=provider_model(environ),
            max_tokens=max_output_tokens,
        )
    except Exception:
        return _failed_answer("UNKNOWN", _SAFE_GATEWAY_REASON, latency_ms=_elapsed(started))
    finally:
        await _close_resource(provider)
        await _close_resource(client)


def _elapsed(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1_000))


__all__ = ["CoachRuntimeRequest", "answer_coach_runtime"]
