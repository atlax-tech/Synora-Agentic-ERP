"""Frappe authority for validated, durable Coach claim provenance."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import frappe

from synora_agentic_erp.agent.service import (
    _RUNTIME_RESPONSE_BYTES,
    _RUNTIME_TIMEOUT_SECONDS,
    _NoRedirectHandler,
    _runtime_url,
)
from synora_agentic_erp.gateway.contract import GatewayFault, canonical_uuid
from synora_agentic_erp.gateway.security import resolve_run
from synora_agentic_erp.memory.service import _actor, _run_scope
from synora_agentic_erp.synora_agentic_erp.doctype.synora_coach_claim.synora_coach_claim import (
    MAX_SOURCE_SNAPSHOT_LENGTH,
    SERVICE_FLAG,
)
from synora_agentic_erp.synora_agentic_erp.doctype.synora_coach_result.synora_coach_result import (
    SERVICE_FLAG as RESULT_SERVICE_FLAG,
)

MAX_CLAIM_LENGTH = 4_000
MAX_REVISION_LENGTH = 140
_RUNTIME_TOKEN_ENV = "SYNORA_RUNTIME_TOKEN"
_CLAIM_HMAC_DOMAIN = b"synora-coach-claim-v1"
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CLAIM_TYPES = frozenset({"ERP_FACT", "RETRIEVED_KNOWLEDGE", "RECOMMENDATION"})
_LIVE_FACT_FIELDS = {
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
_SNAPSHOT_FIELDS = frozenset(
    {
        "run_id",
        "document",
        "scope",
        "state_version",
        "captured_at",
        "source_modified_at",
        "frappe_revision",
        "erpnext_revision",
    }
)
_PACKAGE_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "correlation_id",
        "claim_id",
        "ordinal",
        "claim_type",
        "claim_text",
        "claim_digest",
        "citation_provenance",
        "citation_digest",
        "source_revision",
        "source_snapshot",
        "signature",
    }
)
_CLAIM_FIELDS = [
    "name",
    "run",
    "correlation_id",
    "initiator",
    "company_scope",
    "warehouse_scope",
    "claim_digest",
    "citation_digest",
    "source_revision",
    "source_snapshot",
    "creation",
]
_CAPABILITY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_COACH_ANSWER_FIELDS = frozenset(
    {
        "schema_version",
        "answer_status",
        "answer",
        "claims",
        "citations",
        "refusal_reason",
        "retrieval_trace",
        "token_usage",
        "latency_ms",
        "validated_claims",
    }
)
_COACH_CLAIM_FIELDS = frozenset({"claim_id", "ordinal", "claim_type", "text", "citation_refs"})
_COACH_TRACE_FIELDS = frozenset(
    {
        "selected_chunk_ids",
        "selected_content_digests",
        "selected_revisions",
        "live_fact_digests",
        "provider_tools",
        "context_fragment_ids",
    }
)
_COACH_USAGE_FIELDS = frozenset({"prompt_tokens", "completion_tokens", "reasoning_tokens"})
_COACH_STATUSES = frozenset({"ANSWERED", "UNKNOWN", "CONFLICT", "REFUSED"})
_COACH_SIGNABLE_TYPES = frozenset({"ERP_FACT", "RETRIEVED_KNOWLEDGE", "RECOMMENDATION"})
_COACH_SAFE_REFUSAL_REASONS = {
    "UNKNOWN": "Coach could not produce a grounded answer",
    "REFUSED": "Coach declined to answer",
}
_RESULT_REFUSAL_REASONS = frozenset(
    {
        "Coach could not produce a grounded answer",
        "Coach declined to answer",
        "CONTEXT_REQUIRED",
    }
)
_RESULT_JSON_MAX_LENGTH = 256_000
_RESULT_CLAIM_RECORD_MAX = 32
_RESULT_CITATION_MAX = 64
_RESULT_FIELDS = [
    "name",
    "run",
    "correlation_id",
    "purpose",
    "answer_status",
    "answer",
    "refusal_reason",
    "current_doctype",
    "current_name",
    "claim_records_json",
    "claims_json",
    "citations_json",
    "trace_json",
    "usage_json",
    "latency_ms",
    "creation",
]


def _not_available() -> GatewayFault:
    return GatewayFault("COACH_CLAIM_NOT_AVAILABLE", "Coach claim is not available", 404)


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise GatewayFault("INVALID_INPUT", f"{label} is invalid")
    return value


def _digest(value: object, label: str) -> str:
    text = _text(value, label, 64)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise GatewayFault("INVALID_INPUT", f"{label} is invalid")
    return text


def _canonical_payload(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, (dict, list, str, int, float, bool, type(None))):
        raise GatewayFault("INVALID_INPUT", f"{label} is invalid")
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise GatewayFault("INVALID_INPUT", f"{label} is invalid") from error
    if len(payload) > maximum:
        raise GatewayFault("INVALID_INPUT", f"{label} is too large")
    return payload


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _contains_sensitive_text(value: object, sensitive_values: tuple[str, ...]) -> bool:
    pending: list[object] = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            if any(secret in current for secret in sensitive_values):
                return True
        elif isinstance(current, dict):
            for key, nested in current.items():
                pending.extend((key, nested))
        elif isinstance(current, list):
            pending.extend(current)
    return False


def _strict_mapping(value: object, label: str, fields: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise GatewayFault("INVALID_INPUT", f"{label} fields are invalid")
    return value


def _bounded_int(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise GatewayFault("INVALID_INPUT", f"{label} is invalid")
    return value


def _identifier(value: object, label: str) -> str:
    text = _text(value, label, 120)
    if not _ID_PATTERN.fullmatch(text):
        raise GatewayFault("INVALID_INPUT", f"{label} is invalid")
    return text


def _optional_metadata(value: object, label: str, maximum: int = 140) -> str | None:
    if value is None:
        return None
    return _text(value, label, maximum)


def _validate_citation(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or "citation_type" not in value:
        raise GatewayFault("INVALID_INPUT", "citation provenance is invalid")
    citation_type = value.get("citation_type")
    if citation_type == "LIVE_ERP":
        fields = frozenset(
            {
                "citation_type",
                "citation_id",
                "run_id",
                "document_doctype",
                "document_name",
                "state_version",
                "captured_at",
                "source_modified_at",
                "frappe_revision",
                "erpnext_revision",
                "fact_fields",
                "fact_digest",
            }
        )
        citation = _strict_mapping(value, "live citation", fields)
        if citation["citation_type"] != "LIVE_ERP":
            raise GatewayFault("INVALID_INPUT", "citation type is invalid")
        _identifier(citation["citation_id"], "citation_id")
        canonical_uuid(citation["run_id"], "citation.run_id")
        if citation["document_doctype"] not in {"Material Request", "Purchase Order"}:
            raise GatewayFault("INVALID_INPUT", "citation document type is invalid")
        _text(citation["document_name"], "citation.document_name", 140)
        _bounded_int(citation["state_version"], "citation.state_version", 1, 1_000_000)
        _text(citation["captured_at"], "citation.captured_at", 140)
        _optional_metadata(citation["source_modified_at"], "citation.source_modified_at")
        _optional_metadata(citation["frappe_revision"], "citation.frappe_revision")
        _optional_metadata(citation["erpnext_revision"], "citation.erpnext_revision")
        fact_fields = citation["fact_fields"]
        if (
            not isinstance(fact_fields, list)
            or not 1 <= len(fact_fields) <= 16
            or not all(isinstance(field, str) for field in fact_fields)
            or len(set(fact_fields)) != len(fact_fields)
            or any(
                field not in _LIVE_FACT_FIELDS[citation["document_doctype"]]
                for field in fact_fields
            )
        ):
            raise GatewayFault("INVALID_INPUT", "citation.fact_fields is invalid")
        _digest(citation["fact_digest"], "citation.fact_digest")
        return citation
    if citation_type == "RETRIEVAL":
        fields = frozenset(
            {
                "citation_type",
                "citation_id",
                "chunk_id",
                "content_digest",
                "ordinal",
                "source_type",
                "revision",
                "erp_version",
                "permission_scope",
            }
        )
        citation = _strict_mapping(value, "retrieval citation", fields)
        _identifier(citation["citation_id"], "citation_id")
        _digest(citation["chunk_id"], "citation.chunk_id")
        _digest(citation["content_digest"], "citation.content_digest")
        _bounded_int(citation["ordinal"], "citation.ordinal", 1, 1_000_000)
        for fieldname in ("source_type", "revision", "erp_version", "permission_scope"):
            _text(citation[fieldname], f"citation.{fieldname}", 140)
        return citation
    raise GatewayFault("INVALID_INPUT", "unsupported citation type")


def _validate_source_snapshot(
    value: object, *, run_id: str, source_revision: str
) -> tuple[str, dict[str, Any]]:
    snapshot_json = _text(value, "source_snapshot", MAX_SOURCE_SNAPSHOT_LENGTH)
    try:
        snapshot = json.loads(
            snapshot_json,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise GatewayFault("INVALID_INPUT", "source_snapshot is invalid") from error
    if not isinstance(snapshot, dict) or set(snapshot) != _SNAPSHOT_FIELDS:
        raise GatewayFault("INVALID_INPUT", "source_snapshot fields are invalid")
    try:
        if (
            json.dumps(
                snapshot, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
            )
            != snapshot_json
        ):
            raise GatewayFault("INVALID_INPUT", "source_snapshot is not canonical")
    except (TypeError, ValueError) as error:
        raise GatewayFault("INVALID_INPUT", "source_snapshot is invalid") from error
    if canonical_uuid(snapshot["run_id"], "source_snapshot.run_id") != run_id:
        raise GatewayFault("INVALID_INPUT", "source_snapshot run does not match claim")
    document = _strict_mapping(
        snapshot["document"], "source_snapshot.document", frozenset({"doctype", "name"})
    )
    if document["doctype"] not in {"Material Request", "Purchase Order"}:
        raise GatewayFault("INVALID_INPUT", "source_snapshot document type is invalid")
    _text(document["name"], "source_snapshot.document.name", 140)
    scope = _strict_mapping(
        snapshot["scope"], "source_snapshot.scope", frozenset({"company", "warehouse", "coverage"})
    )
    _text(scope["company"], "source_snapshot.scope.company", 140)
    _optional_metadata(scope["warehouse"], "source_snapshot.scope.warehouse")
    if scope["coverage"] not in {"FULL_DOCUMENT", "WAREHOUSE_SCOPED"}:
        raise GatewayFault("INVALID_INPUT", "source_snapshot scope coverage is invalid")
    expected_coverage = "WAREHOUSE_SCOPED" if scope["warehouse"] else "FULL_DOCUMENT"
    if scope["coverage"] != expected_coverage:
        raise GatewayFault("INVALID_INPUT", "source_snapshot scope is inconsistent")
    _bounded_int(snapshot["state_version"], "source_snapshot.state_version", 1, 1_000_000)
    _text(snapshot["captured_at"], "source_snapshot.captured_at", 140)
    _optional_metadata(snapshot["source_modified_at"], "source_snapshot.source_modified_at")
    _optional_metadata(snapshot["frappe_revision"], "source_snapshot.frappe_revision")
    _optional_metadata(snapshot["erpnext_revision"], "source_snapshot.erpnext_revision")
    revisions = [snapshot["frappe_revision"], snapshot["erpnext_revision"]]
    available_revision = next((revision for revision in revisions if revision), None)
    if available_revision is not None and source_revision not in revisions:
        raise GatewayFault("INVALID_INPUT", "source_revision does not match source snapshot")
    if available_revision is None:
        expected = f"snapshot-{hashlib.sha256(snapshot_json.encode('utf-8')).hexdigest()}"
        if source_revision != expected:
            raise GatewayFault("INVALID_INPUT", "source_revision does not match snapshot digest")
    return snapshot_json, snapshot


def _claim_signing_key(token: str) -> bytes:
    return hmac.new(token.encode("utf-8"), _CLAIM_HMAC_DOMAIN, hashlib.sha256).digest()


def _claim_signature(payload: Mapping[str, object], token: str) -> str:
    canonical = json.dumps(
        dict(payload), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hmac.new(_claim_signing_key(token), canonical, hashlib.sha256).hexdigest()


def _validated_package(value: object) -> dict[str, Any]:
    package = _strict_mapping(value, "validated Coach claim", _PACKAGE_FIELDS)
    if package["schema_version"] != "1":
        raise GatewayFault("INVALID_INPUT", "validated Coach claim schema is invalid")
    safe_run = canonical_uuid(package["run_id"], "run_id")
    safe_correlation = canonical_uuid(package["correlation_id"], "correlation_id")
    _identifier(package["claim_id"], "claim_id")
    _bounded_int(package["ordinal"], "ordinal", 1, 32)
    claim_type = package["claim_type"]
    if claim_type not in _CLAIM_TYPES:
        raise GatewayFault("INVALID_INPUT", "claim_type is invalid")
    claim_text = _text(package["claim_text"], "claim_text", MAX_CLAIM_LENGTH)
    claim_digest = _digest(package["claim_digest"], "claim_digest")
    if claim_digest != hashlib.sha256(claim_text.encode("utf-8")).hexdigest():
        raise GatewayFault("INVALID_INPUT", "claim_digest does not match claim_text")
    provenance = _strict_mapping(
        package["citation_provenance"],
        "citation_provenance",
        frozenset({"citations"}),
    )
    citations = provenance["citations"]
    if not isinstance(citations, list) or not 1 <= len(citations) <= 8:
        raise GatewayFault("INVALID_INPUT", "citation provenance is invalid")
    validated_citations = [_validate_citation(citation) for citation in citations]
    citation_ids = [str(citation["citation_id"]) for citation in validated_citations]
    if len(set(citation_ids)) != len(citation_ids):
        raise GatewayFault("INVALID_INPUT", "citation ids must be unique")
    if claim_type == "ERP_FACT" and any(
        citation["citation_type"] != "LIVE_ERP" for citation in validated_citations
    ):
        raise GatewayFault("INVALID_INPUT", "ERP_FACT requires live citations")
    if claim_type == "RETRIEVED_KNOWLEDGE" and any(
        citation["citation_type"] != "RETRIEVAL" for citation in validated_citations
    ):
        raise GatewayFault("INVALID_INPUT", "retrieved knowledge requires retrieval citations")
    normalized_provenance = {"citations": validated_citations}
    provenance_json = _canonical_payload(
        normalized_provenance, "citation_provenance", MAX_SOURCE_SNAPSHOT_LENGTH
    )
    citation_digest = _digest(package["citation_digest"], "citation_digest")
    if citation_digest != hashlib.sha256(provenance_json.encode("utf-8")).hexdigest():
        raise GatewayFault("INVALID_INPUT", "citation_digest does not match provenance")
    source_revision = _text(package["source_revision"], "source_revision", MAX_REVISION_LENGTH)
    snapshot_json, snapshot = _validate_source_snapshot(
        package["source_snapshot"], run_id=safe_run, source_revision=source_revision
    )
    signature = _digest(package["signature"], "signature")
    token = os.environ.get(_RUNTIME_TOKEN_ENV, "").strip()
    if not token:
        raise GatewayFault("UNAVAILABLE", "Coach claim validation is unavailable", 503)
    unsigned = dict(package)
    unsigned["citation_provenance"] = normalized_provenance
    unsigned["source_snapshot"] = snapshot_json
    unsigned.pop("signature", None)
    if not hmac.compare_digest(signature, _claim_signature(unsigned, token)):
        raise GatewayFault("INVALID_INPUT", "validated Coach claim signature is invalid")
    # Keep caller input immutable from this point on; all persistence derives
    # from the canonical, validated representation above.
    package = dict(unsigned)
    package["signature"] = signature
    package["_run_id"] = safe_run
    package["_correlation_id"] = safe_correlation
    package["_claim_digest"] = claim_digest
    package["_citation_digest"] = citation_digest
    package["_source_revision"] = source_revision
    package["_snapshot"] = snapshot
    return package


def _dedupe_key(
    *, run_id: str, claim_digest: str, citation_digest: str, source_revision: str
) -> str:
    payload = json.dumps(
        {
            "run": run_id,
            "claim_digest": claim_digest,
            "citation_digest": citation_digest,
            "source_revision": source_revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _serialize(row: Any) -> dict[str, Any]:
    return {
        "name": str(row.name),
        "run": str(row.run),
        "correlation_id": str(row.correlation_id or "") or None,
        "initiator": str(row.initiator),
        "company_scope": str(row.company_scope),
        "warehouse_scope": str(row.warehouse_scope or "") or None,
        "claim_digest": str(row.claim_digest),
        "citation_digest": str(row.citation_digest),
        "source_revision": str(row.source_revision),
        "source_snapshot": str(row.source_snapshot),
        "created_at": str(row.creation or ""),
    }


def _load_by_dedupe(dedupe_key: str) -> Any | None:
    row = frappe.db.get_value(
        "Synora Coach Claim", {"dedupe_key": dedupe_key}, _CLAIM_FIELDS, as_dict=True
    )
    if not row:
        return None
    row["doctype"] = "Synora Coach Claim"
    return frappe.get_doc(row)


def persist_coach_claim(
    *,
    validated_claim: object,
) -> dict[str, Any]:
    """Persist only a Runtime-signed, fully validated claim package."""
    actor = _actor()
    package = _validated_package(validated_claim)
    safe_run = str(package["_run_id"])
    scope = _run_scope(safe_run, actor)
    safe_correlation = str(package["_correlation_id"])
    if safe_correlation != str(scope.get("correlation_id") or ""):
        raise _not_available()
    safe_claim_digest = str(package["_claim_digest"])
    safe_citation_digest = str(package["_citation_digest"])
    safe_revision = str(package["_source_revision"])
    snapshot_json = str(package["source_snapshot"])
    snapshot = package["_snapshot"]
    if not isinstance(snapshot, dict):  # pragma: no cover - validated above
        raise GatewayFault("INVALID_INPUT", "source_snapshot is invalid")
    if str(snapshot["scope"]["company"]) != str(scope["company"]) or (
        str(snapshot["scope"]["warehouse"] or "") or None
    ) != (str(scope["warehouse"] or "") or None):
        raise _not_available()
    for citation in package["citation_provenance"]["citations"]:
        if citation["citation_type"] == "LIVE_ERP":
            if (
                str(citation["run_id"]) != safe_run
                or citation["document_doctype"] != snapshot["document"]["doctype"]
                or str(citation["document_name"]) != str(snapshot["document"]["name"])
                or citation["state_version"] != snapshot["state_version"]
                or citation["captured_at"] != snapshot["captured_at"]
                or citation["source_modified_at"] != snapshot["source_modified_at"]
                or citation["frappe_revision"] != snapshot["frappe_revision"]
                or citation["erpnext_revision"] != snapshot["erpnext_revision"]
            ):
                raise _not_available()
    dedupe_key = _dedupe_key(
        run_id=safe_run,
        claim_digest=safe_claim_digest,
        citation_digest=safe_citation_digest,
        source_revision=safe_revision,
    )
    existing = _load_by_dedupe(dedupe_key)
    if existing is not None:
        if (
            str(existing.run) != safe_run
            or str(existing.initiator) != str(scope["initiator"])
            or str(existing.company_scope) != str(scope["company"])
            or (str(existing.warehouse_scope or "") or None)
            != (str(scope["warehouse"] or "") or None)
        ):
            raise _not_available()
        return {"created": False, **_serialize(existing)}
    values = {
        "doctype": "Synora Coach Claim",
        "run": safe_run,
        "correlation_id": safe_correlation,
        "initiator": scope["initiator"],
        "company_scope": scope["company"],
        "warehouse_scope": scope["warehouse"],
        "claim_digest": safe_claim_digest,
        "citation_digest": safe_citation_digest,
        "source_revision": safe_revision,
        "source_snapshot": snapshot_json,
        "dedupe_key": dedupe_key,
    }
    doc = frappe.get_doc(values)
    doc.flags[SERVICE_FLAG] = True
    try:
        inserted = doc.insert(ignore_permissions=True)
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError) as error:
        winner = _load_by_dedupe(dedupe_key)
        if winner is None:
            raise GatewayFault(
                "CONFLICT", "Coach claim duplicate race is unresolved", 409
            ) from error
        if str(winner.run) != safe_run or str(winner.initiator) != str(scope["initiator"]):
            raise _not_available() from None
        return {"created": False, **_serialize(winner)}
    return {"created": True, **_serialize(inserted)}


def _coach_run_not_available() -> GatewayFault:
    """Use one opaque result for every Run/capability visibility failure."""
    return GatewayFault("COACH_RUN_NOT_AVAILABLE", "Coach run is not available", 404)


def _coach_capability(value: object) -> str:
    capability = _text(value, "capability", 43)
    if not _CAPABILITY_PATTERN.fullmatch(capability):
        raise GatewayFault("INVALID_INPUT", "capability is invalid")
    return capability


def validate_coach_capability(value: object) -> str:
    """Validate the existing raw Run capability without storing or rotating it."""
    return _coach_capability(value)


def _coach_response_invalid() -> GatewayFault:
    return GatewayFault("COACH_RESPONSE_INVALID", "Coach runtime returned an invalid answer", 502)


def _coach_claims_not_persisted() -> GatewayFault:
    return GatewayFault("COACH_CLAIMS_NOT_PERSISTED", "Coach claims could not be persisted", 503)


def _coach_result_not_persisted() -> GatewayFault:
    return GatewayFault("COACH_RESULT_NOT_PERSISTED", "Coach result could not be persisted", 503)


def _coach_result_not_available() -> GatewayFault:
    return GatewayFault("COACH_RESULT_NOT_AVAILABLE", "Coach result is not available", 404)


def _call_coach_runtime(payload: dict[str, object], capability: str) -> dict[str, Any]:
    """Call Runtime through the existing loopback/host-gateway policy."""
    runtime_token = os.environ.get(_RUNTIME_TOKEN_ENV, "").strip()
    if not runtime_token:
        raise GatewayFault("UNAVAILABLE", "Coach runtime authentication is unavailable", 503)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Synora-Runtime-Token": runtime_token,
    }
    request: Any = None
    encoded_payload = b""
    raw = b""
    try:
        encoded_payload = json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        request = urllib.request.Request(
            _runtime_url("coach/answer"),
            data=encoded_payload,
            headers=headers,
            method="POST",
        )
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=_RUNTIME_TIMEOUT_SECONDS) as response:
            raw = response.read(_RUNTIME_RESPONSE_BYTES + 1)
        sensitive_values = (capability.encode("utf-8"), runtime_token.encode("utf-8"))
        if len(raw) > _RUNTIME_RESPONSE_BYTES or any(value in raw for value in sensitive_values):
            raise ValueError("runtime response is unsafe")
        body = json.loads(
            raw,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_pairs,
        )
        if not isinstance(body, dict) or _contains_sensitive_text(
            body, (capability, runtime_token)
        ):
            raise ValueError("runtime response must be an object")
        return body
    except GatewayFault:
        raise
    except urllib.error.HTTPError:
        raise GatewayFault("UNAVAILABLE", "Coach runtime is unavailable", 503) from None
    except urllib.error.URLError, TimeoutError, OSError:
        raise GatewayFault("UNAVAILABLE", "Coach runtime is unavailable", 503) from None
    except TypeError, UnicodeError, ValueError:
        raise _coach_response_invalid() from None
    finally:
        payload.clear()
        headers.clear()
        encoded_payload = b""
        raw = b""
        request = None
        runtime_token = ""


def _coach_list(value: object, label: str, maximum: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label} is invalid")
    return value


def _coach_output_claim(value: object) -> dict[str, Any]:
    claim = _strict_mapping(value, "Coach claim", _COACH_CLAIM_FIELDS)
    claim_type = claim["claim_type"]
    if claim_type not in _COACH_SIGNABLE_TYPES:
        raise ValueError("claim type is invalid")
    references = claim["citation_refs"]
    if not isinstance(references, list) or not 1 <= len(references) <= 8:
        raise ValueError("claim citation refs are invalid")
    safe_references = [_identifier(reference, "citation_ref") for reference in references]
    if len(set(safe_references)) != len(safe_references):
        raise ValueError("claim citation refs are not unique")
    return {
        "claim_id": _identifier(claim["claim_id"], "claim_id"),
        "ordinal": _bounded_int(claim["ordinal"], "ordinal", 1, 32),
        "claim_type": claim_type,
        "text": _text(claim["text"], "claim text", MAX_CLAIM_LENGTH),
        "citation_refs": safe_references,
    }


def _coach_output_trace(value: object) -> dict[str, list[str]]:
    trace = _strict_mapping(value, "retrieval trace", _COACH_TRACE_FIELDS)
    limits = {
        "selected_chunk_ids": 5,
        "selected_content_digests": 5,
        "selected_revisions": 5,
        "live_fact_digests": 50,
        "provider_tools": 0,
        "context_fragment_ids": 64,
    }
    safe: dict[str, list[str]] = {}
    for field, maximum in limits.items():
        values = trace[field]
        if not isinstance(values, list) or len(values) > maximum:
            raise ValueError("retrieval trace collection is invalid")
        if any(not isinstance(item, str) for item in values):
            raise ValueError("retrieval trace collection is invalid")
        if field in {"selected_chunk_ids", "selected_content_digests", "live_fact_digests"}:
            safe[field] = [_digest(item, f"retrieval trace {field}") for item in values]
        else:
            safe[field] = [_text(item, f"retrieval trace {field}", 140) for item in values]
    if safe["provider_tools"]:
        raise ValueError("provider tools are not allowed")
    return safe


def _coach_output_usage(value: object) -> dict[str, int]:
    usage = _strict_mapping(value, "token usage", _COACH_USAGE_FIELDS)
    return {
        field: _bounded_int(usage[field], field, 0, 10_000_000)
        for field in ("prompt_tokens", "completion_tokens", "reasoning_tokens")
    }


def _coach_output_package(
    value: object,
    *,
    expected_run: str,
    expected_correlation: str,
    expected_doctype: str,
    expected_name: str,
    expected_scope: Mapping[str, str | None],
) -> dict[str, Any]:
    package = _strict_mapping(value, "validated Coach claim", _PACKAGE_FIELDS)
    if package["schema_version"] != "1":
        raise ValueError("validated claim schema is invalid")
    safe_run = canonical_uuid(package["run_id"], "validated claim run_id")
    safe_correlation = canonical_uuid(package["correlation_id"], "validated claim correlation_id")
    if safe_run != expected_run or safe_correlation != expected_correlation:
        raise ValueError("validated claim identity is invalid")
    claim_id = _identifier(package["claim_id"], "validated claim id")
    ordinal = _bounded_int(package["ordinal"], "validated claim ordinal", 1, 32)
    claim_type = package["claim_type"]
    if claim_type not in _COACH_SIGNABLE_TYPES:
        raise ValueError("validated claim type is invalid")
    claim_text = _text(package["claim_text"], "validated claim text", MAX_CLAIM_LENGTH)
    claim_digest = _digest(package["claim_digest"], "validated claim digest")
    if claim_digest != hashlib.sha256(claim_text.encode("utf-8")).hexdigest():
        raise ValueError("validated claim digest is invalid")

    provenance = _strict_mapping(
        package["citation_provenance"],
        "validated citation provenance",
        frozenset({"citations"}),
    )
    raw_citations = provenance["citations"]
    if not isinstance(raw_citations, list) or not 1 <= len(raw_citations) <= 8:
        raise ValueError("validated citation provenance is invalid")
    citations = [_validate_citation(citation) for citation in raw_citations]
    citation_ids = [str(citation["citation_id"]) for citation in citations]
    if len(set(citation_ids)) != len(citation_ids):
        raise ValueError("validated citation ids are not unique")
    if claim_type == "ERP_FACT" and any(
        citation["citation_type"] != "LIVE_ERP" for citation in citations
    ):
        raise ValueError("validated ERP claim citations are invalid")
    if claim_type == "RETRIEVED_KNOWLEDGE" and any(
        citation["citation_type"] != "RETRIEVAL" for citation in citations
    ):
        raise ValueError("validated retrieval claim citations are invalid")
    normalized_provenance = {"citations": citations}
    provenance_json = _canonical_payload(
        normalized_provenance, "validated citation provenance", MAX_SOURCE_SNAPSHOT_LENGTH
    )
    citation_digest = _digest(package["citation_digest"], "validated citation digest")
    if citation_digest != hashlib.sha256(provenance_json.encode("utf-8")).hexdigest():
        raise ValueError("validated citation digest is invalid")

    source_revision = _text(
        package["source_revision"], "validated source revision", MAX_REVISION_LENGTH
    )
    source_snapshot, snapshot = _validate_source_snapshot(
        package["source_snapshot"], run_id=expected_run, source_revision=source_revision
    )
    document = snapshot["document"]
    scope = snapshot["scope"]
    if (
        not isinstance(document, dict)
        or document.get("doctype") != expected_doctype
        or document.get("name") != expected_name
        or not isinstance(scope, dict)
        or scope.get("company") != expected_scope.get("company")
        or (scope.get("warehouse") or None) != (expected_scope.get("warehouse") or None)
    ):
        raise ValueError("validated source snapshot is outside the request scope")
    _digest(package["signature"], "validated claim signature")
    normalized = dict(package)
    normalized.update(
        {
            "run_id": safe_run,
            "correlation_id": safe_correlation,
            "claim_id": claim_id,
            "ordinal": ordinal,
            "claim_text": claim_text,
            "claim_digest": claim_digest,
            "citation_provenance": normalized_provenance,
            "citation_digest": citation_digest,
            "source_revision": source_revision,
            "source_snapshot": source_snapshot,
        }
    )
    return normalized


def _validate_coach_answer(
    value: object,
    *,
    expected_run: str,
    expected_correlation: str,
    expected_doctype: str,
    expected_name: str,
    expected_scope: Mapping[str, str | None],
) -> dict[str, Any]:
    """Strictly validate Runtime's complete Coach envelope before persistence."""
    try:
        body = _strict_mapping(value, "Coach answer", _COACH_ANSWER_FIELDS)
        if body["schema_version"] != "1" or body["answer_status"] not in _COACH_STATUSES:
            raise ValueError("Coach answer identity is invalid")
        status = str(body["answer_status"])
        answer = body["answer"]
        if not isinstance(answer, str) or len(answer) > 8_000:
            raise ValueError("Coach answer text is invalid")
        refusal_reason = body["refusal_reason"]
        if refusal_reason is not None:
            refusal_reason = _text(refusal_reason, "refusal reason", 500)
        claims = [_coach_output_claim(item) for item in _coach_list(body["claims"], "claims", 32)]
        citations = [
            _validate_citation(item) for item in _coach_list(body["citations"], "citations", 64)
        ]
        citation_ids = [str(citation["citation_id"]) for citation in citations]
        if len(set(citation_ids)) != len(citation_ids):
            raise ValueError("Coach citation ids are not unique")
        claim_ids = [claim["claim_id"] for claim in claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("Coach claim ids are not unique")
        if [claim["ordinal"] for claim in claims] != list(range(1, len(claims) + 1)):
            raise ValueError("Coach claim ordinals are not contiguous")
        citation_map = dict(zip(citation_ids, citations, strict=True))
        referenced: set[str] = set()
        for claim in claims:
            for reference in claim["citation_refs"]:
                citation = citation_map.get(reference)
                if citation is None:
                    raise ValueError("Coach citation reference is missing")
                referenced.add(reference)
                if claim["claim_type"] == "ERP_FACT" and citation["citation_type"] != "LIVE_ERP":
                    raise ValueError("Coach ERP citation type is invalid")
                if (
                    claim["claim_type"] == "RETRIEVED_KNOWLEDGE"
                    and citation["citation_type"] != "RETRIEVAL"
                ):
                    raise ValueError("Coach retrieval citation type is invalid")
        if referenced != set(citation_ids):
            raise ValueError("orphan Coach citations are not allowed")

        packages = [
            _coach_output_package(
                item,
                expected_run=expected_run,
                expected_correlation=expected_correlation,
                expected_doctype=expected_doctype,
                expected_name=expected_name,
                expected_scope=expected_scope,
            )
            for item in _coach_list(body["validated_claims"], "validated claims", 32)
        ]
        package_ids = [package["claim_id"] for package in packages]
        if len(set(package_ids)) != len(package_ids):
            raise ValueError("validated claim ids are not unique")

        if status in {"UNKNOWN", "REFUSED"}:
            if answer.strip() or claims or citations or packages or not refusal_reason:
                raise ValueError("non-answer Coach status contains answer data")
        else:
            if not answer.strip() or not claims or not citations or refusal_reason is not None:
                raise ValueError("displayable Coach status is incomplete")
            if answer != "\n".join(claim["text"] for claim in claims):
                raise ValueError("Coach answer is not rebuilt from claims")
            if set(package_ids) != set(claim_ids) or len(packages) != len(claims):
                raise ValueError("every display claim requires one validated package")
            packages_by_id = {package["claim_id"]: package for package in packages}
            for claim in claims:
                package = packages_by_id[claim["claim_id"]]
                if (
                    package["ordinal"] != claim["ordinal"]
                    or package["claim_type"] != claim["claim_type"]
                    or package["claim_text"] != claim["text"]
                ):
                    raise ValueError("validated claim does not match display claim")
                package_citations = package["citation_provenance"]["citations"]
                package_refs = [str(citation["citation_id"]) for citation in package_citations]
                if package_refs != claim["citation_refs"]:
                    raise ValueError("validated claim provenance does not match refs")
                for reference, package_citation in zip(
                    claim["citation_refs"], package_citations, strict=True
                ):
                    if _canonical_payload(
                        package_citation, "citation", MAX_SOURCE_SNAPSHOT_LENGTH
                    ) != _canonical_payload(
                        citation_map[reference], "citation", MAX_SOURCE_SNAPSHOT_LENGTH
                    ):
                        raise ValueError("validated claim provenance does not match citation")
            if [package["ordinal"] for package in packages] != list(range(1, len(packages) + 1)):
                raise ValueError("validated claim ordinals are not contiguous")

        return {
            "answer_status": status,
            "answer": answer,
            "refusal_reason": (
                None if refusal_reason is None else _COACH_SAFE_REFUSAL_REASONS[status]
            ),
            "claims": claims,
            "citations": citations,
            "retrieval_trace": _coach_output_trace(body["retrieval_trace"]),
            "token_usage": _coach_output_usage(body["token_usage"]),
            "latency_ms": _bounded_int(body["latency_ms"], "latency_ms", 0, 86_400_000),
            "validated_claims": packages,
        }
    except GatewayFault, KeyError, TypeError, ValueError:
        raise _coach_response_invalid() from None


def _persisted_provenance(
    value: object,
    package: Mapping[str, Any],
    *,
    expected_scope: Mapping[str, str | None],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("persisted claim is invalid")
    if (
        str(value.get("run")) != str(package["run_id"])
        or str(value.get("correlation_id")) != str(package["correlation_id"])
        or str(value.get("company_scope")) != str(expected_scope["company"])
        or (str(value.get("warehouse_scope") or "") or None)
        != (str(expected_scope.get("warehouse") or "") or None)
        or str(value.get("claim_digest")) != str(package["claim_digest"])
        or str(value.get("citation_digest")) != str(package["citation_digest"])
        or str(value.get("source_revision")) != str(package["source_revision"])
    ):
        raise ValueError("persisted claim does not match package")
    return {
        "claim_id": package["claim_id"],
        "ordinal": package["ordinal"],
        "claim_type": package["claim_type"],
        "claim_digest": package["claim_digest"],
        "citation_digest": package["citation_digest"],
        "source_revision": package["source_revision"],
        "persisted_claim_id": _text(value.get("name"), "persisted claim id", 140),
    }


def _persist_coach_claims(
    packages: list[dict[str, Any]],
    *,
    expected_scope: Mapping[str, str | None],
) -> list[dict[str, Any]]:
    if not packages:
        return []
    savepoint = f"synora_coach_claims_{uuid4().hex}"
    try:
        frappe.db.savepoint(savepoint)
        persisted = [
            _persisted_provenance(
                persist_coach_claim(validated_claim=package),
                package,
                expected_scope=expected_scope,
            )
            for package in packages
        ]
        return persisted
    except Exception:
        try:
            frappe.db.rollback(save_point=savepoint)
        except Exception:
            pass
        raise _coach_claims_not_persisted() from None


def _result_json(value: object, label: str) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise _coach_result_not_persisted() from error
    if len(encoded) > _RESULT_JSON_MAX_LENGTH:
        raise _coach_result_not_persisted()
    return encoded


def _coach_envelope(
    validated: Mapping[str, Any], persisted: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "answer_status": validated["answer_status"],
        "answer": validated["answer"],
        "refusal_reason": validated["refusal_reason"],
        "claims": validated["claims"],
        "citations": validated["citations"],
        "retrieval_trace": validated["retrieval_trace"],
        "token_usage": validated["token_usage"],
        "latency_ms": validated["latency_ms"],
        "provenance": persisted,
    }


def _result_document_values(
    *,
    run_id: str,
    correlation_id: str,
    current_doctype: str | None,
    current_name: str | None,
    validated: Mapping[str, Any],
    persisted: list[dict[str, Any]],
) -> dict[str, Any]:
    status = validated.get("answer_status")
    if status not in _COACH_STATUSES:
        raise _coach_result_not_persisted()
    answer = validated.get("answer")
    refusal_reason = validated.get("refusal_reason")
    claims = validated.get("claims")
    citations = validated.get("citations")
    trace = validated.get("retrieval_trace")
    usage = validated.get("token_usage")
    latency_ms = validated.get("latency_ms")
    if not isinstance(answer, str) or len(answer) > 8_000:
        raise _coach_result_not_persisted()
    if refusal_reason is not None and refusal_reason not in _RESULT_REFUSAL_REASONS:
        raise _coach_result_not_persisted()
    if not isinstance(claims, list) or not isinstance(citations, list):
        raise _coach_result_not_persisted()
    if not isinstance(persisted, list) or len(persisted) != len(claims):
        raise _coach_result_not_persisted()
    if current_doctype is None or current_name is None:
        if current_doctype is not None or current_name is not None:
            raise _coach_result_not_persisted()
    elif current_doctype not in {"Material Request", "Purchase Order"}:
        raise _coach_result_not_persisted()
    else:
        _text(current_name, "current_name", 140)
    safe_claims = [
        {
            "claim_id": claim["claim_id"],
            "ordinal": claim["ordinal"],
            "claim_type": claim["claim_type"],
            "text": claim["text"],
            "citation_refs": claim["citation_refs"],
        }
        for claim in claims
        if isinstance(claim, dict)
    ]
    if len(safe_claims) != len(claims):
        raise _coach_result_not_persisted()
    claim_record_ids = []
    for item in persisted:
        if not isinstance(item, dict):
            raise _coach_result_not_persisted()
        claim_record_ids.append(_identifier(item.get("persisted_claim_id"), "claim record id"))
    if status in {"UNKNOWN", "REFUSED"}:
        if answer.strip() or safe_claims or citations or claim_record_ids or not refusal_reason:
            raise _coach_result_not_persisted()
    elif not answer.strip() or not safe_claims or not citations or refusal_reason is not None:
        raise _coach_result_not_persisted()
    return {
        "doctype": "Synora Coach Result",
        "run": run_id,
        "correlation_id": correlation_id,
        "purpose": "ERP_COACH",
        "answer_status": status,
        "answer": answer,
        "refusal_reason": refusal_reason,
        "current_doctype": current_doctype,
        "current_name": current_name,
        "claim_records_json": _result_json(claim_record_ids, "claim records"),
        "claims_json": _result_json(safe_claims, "claims"),
        "citations_json": _result_json(citations, "citations"),
        "trace_json": _result_json(trace, "trace"),
        "usage_json": _result_json(usage, "usage"),
        "latency_ms": latency_ms,
    }


def _persist_coach_result(
    *,
    run_id: str,
    correlation_id: str,
    current_doctype: str | None,
    current_name: str | None,
    validated: Mapping[str, Any],
    persisted: list[dict[str, Any]],
) -> dict[str, str]:
    values = _result_document_values(
        run_id=run_id,
        correlation_id=correlation_id,
        current_doctype=current_doctype,
        current_name=current_name,
        validated=validated,
        persisted=persisted,
    )
    try:
        doc = frappe.get_doc(values)
        doc.flags[RESULT_SERVICE_FLAG] = True
        inserted = doc.insert(ignore_permissions=True)
    except GatewayFault:
        raise
    except Exception:
        raise _coach_result_not_persisted() from None
    return {"result_id": str(inserted.name), "created_at": str(inserted.creation or "")}


def _persist_coach_evidence(
    *,
    validated: Mapping[str, Any],
    expected_scope: Mapping[str, str | None],
    current_doctype: str,
    current_name: str,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    savepoint = f"synora_coach_evidence_{uuid4().hex}"
    try:
        frappe.db.savepoint(savepoint)
        persisted = _persist_coach_claims(
            validated["validated_claims"],
            expected_scope=expected_scope,
        )
        result = _persist_coach_result(
            run_id=str(expected_scope["run_id"]),
            correlation_id=str(expected_scope["correlation_id"]),
            current_doctype=current_doctype,
            current_name=current_name,
            validated=validated,
            persisted=persisted,
        )
        return persisted, result
    except GatewayFault:
        try:
            frappe.db.rollback(save_point=savepoint)
        except Exception:
            pass
        raise
    except Exception:
        try:
            frappe.db.rollback(save_point=savepoint)
        except Exception:
            pass
        raise _coach_result_not_persisted() from None


def _context_required_answer() -> dict[str, Any]:
    return {
        "answer_status": "REFUSED",
        "answer": "",
        "refusal_reason": "CONTEXT_REQUIRED",
        "claims": [],
        "citations": [],
        "retrieval_trace": {
            "selected_chunk_ids": [],
            "selected_content_digests": [],
            "selected_revisions": [],
            "live_fact_digests": [],
            "provider_tools": [],
            "context_fragment_ids": [],
        },
        "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0},
        "latency_ms": 0,
        "validated_claims": [],
    }


def persist_context_required_coach_result(
    *, run_id: object, correlation_id: object
) -> dict[str, Any]:
    """Persist the safe no-context refusal without invoking Runtime or Provider."""
    actor = _actor()
    safe_run = canonical_uuid(run_id, "run_id")
    safe_correlation = canonical_uuid(correlation_id, "correlation_id")
    scope = _run_scope(safe_run, actor)
    if str(scope.get("correlation_id") or "") != safe_correlation:
        raise _coach_run_not_available()
    validated = _context_required_answer()
    savepoint = f"synora_coach_refusal_{uuid4().hex}"
    try:
        frappe.db.savepoint(savepoint)
        result = _persist_coach_result(
            run_id=safe_run,
            correlation_id=safe_correlation,
            current_doctype=None,
            current_name=None,
            validated=validated,
            persisted=[],
        )
    except GatewayFault:
        try:
            frappe.db.rollback(save_point=savepoint)
        except Exception:
            pass
        raise
    except Exception:
        try:
            frappe.db.rollback(save_point=savepoint)
        except Exception:
            pass
        raise _coach_result_not_persisted() from None
    return {"result": result, "coach": _coach_envelope(validated, [])}


def _result_row_value(row: Any, field: str) -> object:
    if isinstance(row, Mapping):
        return row.get(field)
    return getattr(row, field, None)


def _stored_result_json(
    value: object, label: str, expected: type[list[Any]] | type[dict[str, Any]]
) -> Any:
    raw = value if isinstance(value, str) else ""
    if not raw or len(raw) > _RESULT_JSON_MAX_LENGTH:
        raise ValueError(f"{label} is invalid")
    parsed = json.loads(
        raw,
        parse_constant=_reject_json_constant,
        object_pairs_hook=_reject_duplicate_pairs,
    )
    if not isinstance(parsed, expected):
        raise ValueError(f"{label} is invalid")
    canonical = json.dumps(
        parsed, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
    if canonical != raw:
        raise ValueError(f"{label} is not canonical")
    return parsed


def serialize_coach_result(row: Any) -> dict[str, Any]:
    """Return only the immutable, safe presentation model for Runs history."""
    try:
        result_id = _identifier(_result_row_value(row, "name"), "result_id")
        run_id = canonical_uuid(_result_row_value(row, "run"), "run")
        correlation_id = canonical_uuid(_result_row_value(row, "correlation_id"), "correlation_id")
        if _result_row_value(row, "purpose") != "ERP_COACH":
            raise ValueError("purpose is invalid")
        status = _result_row_value(row, "answer_status")
        if status not in _COACH_STATUSES:
            raise ValueError("answer status is invalid")
        answer = _result_row_value(row, "answer")
        if not isinstance(answer, str) or len(answer) > 8_000:
            raise ValueError("answer is invalid")
        refusal_reason = _result_row_value(row, "refusal_reason")
        if refusal_reason is not None and refusal_reason not in _RESULT_REFUSAL_REASONS:
            raise ValueError("refusal reason is invalid")
        raw_claims = _stored_result_json(_result_row_value(row, "claims_json"), "claims", list)
        claims = [_coach_output_claim(item) for item in raw_claims]
        raw_citations = _stored_result_json(
            _result_row_value(row, "citations_json"), "citations", list
        )
        citations = [_validate_citation(item) for item in raw_citations]
        raw_claim_records = _stored_result_json(
            _result_row_value(row, "claim_records_json"), "claim records", list
        )
        claim_record_ids = [_identifier(item, "claim record id") for item in raw_claim_records]
        trace = _coach_output_trace(
            _stored_result_json(_result_row_value(row, "trace_json"), "trace", dict)
        )
        usage = _coach_output_usage(
            _stored_result_json(_result_row_value(row, "usage_json"), "usage", dict)
        )
        latency_ms = _bounded_int(_result_row_value(row, "latency_ms"), "latency_ms", 0, 86_400_000)
        if [claim["ordinal"] for claim in claims] != list(range(1, len(claims) + 1)):
            raise ValueError("claim ordinals are invalid")
        if len(claim_record_ids) != len(claims):
            raise ValueError("claim records do not match claims")
        citation_ids = [str(citation["citation_id"]) for citation in citations]
        if len(set(citation_ids)) != len(citation_ids):
            raise ValueError("citation ids are invalid")
        citation_map = dict(zip(citation_ids, citations, strict=True))
        referenced: set[str] = set()
        for claim in claims:
            for reference in claim["citation_refs"]:
                citation = citation_map.get(reference)
                if citation is None:
                    raise ValueError("claim citation is missing")
                referenced.add(reference)
                if claim["claim_type"] == "ERP_FACT" and citation["citation_type"] != "LIVE_ERP":
                    raise ValueError("ERP claim citation is invalid")
                if (
                    claim["claim_type"] == "RETRIEVED_KNOWLEDGE"
                    and citation["citation_type"] != "RETRIEVAL"
                ):
                    raise ValueError("retrieval claim citation is invalid")
        if referenced != set(citation_ids):
            raise ValueError("orphan citation")
        if status in {"UNKNOWN", "REFUSED"}:
            if answer.strip() or claims or citations or claim_record_ids or not refusal_reason:
                raise ValueError("non-answer result contains answer data")
        elif not answer.strip() or not claims or not citations or refusal_reason is not None:
            raise ValueError("answer result is incomplete")
        elif answer != "\n".join(claim["text"] for claim in claims):
            raise ValueError("answer does not match claims")
        current_doctype = _result_row_value(row, "current_doctype")
        current_name = _result_row_value(row, "current_name")
        # Frappe represents an empty optional Select/Data value as either an
        # empty string or None depending on the read path.
        if current_doctype == "":
            current_doctype = None
        if current_name == "":
            current_name = None
        if current_doctype is None and current_name is None:
            current_document = None
        elif (
            current_doctype in {"Material Request", "Purchase Order"}
            and isinstance(current_name, str)
            and current_name
            and len(current_name) <= 140
        ):
            current_document = {"doctype": current_doctype, "name": current_name}
        else:
            raise ValueError("current document is invalid")
        return {
            "result_id": result_id,
            "run_id": run_id,
            "correlation_id": correlation_id,
            "purpose": "ERP_COACH",
            "answer_status": status,
            "answer": answer,
            "refusal_reason": refusal_reason,
            "current_document": current_document,
            "claims": claims,
            "citations": citations,
            "trace": trace,
            "usage": usage,
            "latency_ms": latency_ms,
            "created_at": str(_result_row_value(row, "creation") or "")[:64],
            "_claim_record_ids": claim_record_ids,
        }
    except GatewayFault, KeyError, TypeError, ValueError, json.JSONDecodeError:
        raise _coach_result_not_available() from None


def answer_contextual_coach(
    *,
    run_id: object,
    capability: object,
    question: object,
    current_doctype: object,
    current_name: object,
) -> dict[str, Any]:
    """Answer a Coach question from one authenticated, server-bound Run."""
    actor = _actor()
    safe_run = canonical_uuid(run_id, "run_id")
    safe_capability = validate_coach_capability(capability)
    safe_question = _text(question, "question", 1_000)
    if not isinstance(current_doctype, str) or current_doctype not in {
        "Material Request",
        "Purchase Order",
    }:
        raise GatewayFault("INVALID_INPUT", "current_doctype is invalid")
    safe_doctype = str(current_doctype)
    safe_name = _text(current_name, "current_name", 140)

    try:
        scope = _run_scope(safe_run, actor)
        resolved = resolve_run(safe_run, safe_capability)
        safe_correlation = canonical_uuid(scope.get("correlation_id"), "run correlation_id")
    except GatewayFault:
        raise _coach_run_not_available() from None
    except Exception:
        raise _coach_run_not_available() from None
    if (
        resolved.run_id != scope["run_id"]
        or str(resolved.initiator) != str(scope["initiator"])
        or str(resolved.company) != str(scope["company"])
        or (str(resolved.warehouse or "") or None) != (str(scope["warehouse"] or "") or None)
    ):
        raise _coach_run_not_available()

    payload: dict[str, object] = {
        "schema_version": "1",
        "run_id": safe_run,
        "correlation_id": safe_correlation,
        "question": safe_question,
        "current_document": {"doctype": safe_doctype, "name": safe_name},
        "capability": safe_capability,
    }
    runtime_answer = _call_coach_runtime(payload, safe_capability)
    validated = _validate_coach_answer(
        runtime_answer,
        expected_run=safe_run,
        expected_correlation=safe_correlation,
        expected_doctype=safe_doctype,
        expected_name=safe_name,
        expected_scope=scope,
    )
    persisted, result = _persist_coach_evidence(
        validated=validated,
        expected_scope=scope,
        current_doctype=safe_doctype,
        current_name=safe_name,
    )
    return {
        "ok": True,
        "schema_version": "1",
        "correlation_id": safe_correlation,
        "result_id": result["result_id"],
        "coach": _coach_envelope(validated, persisted),
    }


def resolve_coach_claim(
    claim_id: object,
    *,
    run_id: object,
    expected_claim_digest: object = None,
    expected_citation_digest: object = None,
    source_revision: object = None,
) -> dict[str, Any]:
    """Resolve a claim only after rechecking the caller's current Run scope."""
    try:
        actor = _actor()
        safe_run = canonical_uuid(run_id, "run_id")
        scope = _run_scope(safe_run, actor)
        safe_claim_id = canonical_uuid(claim_id, "claim_id")
    except Exception as error:
        if isinstance(error, GatewayFault) and error.code == "AUTHENTICATION_REQUIRED":
            raise
        raise _not_available() from None
    row = frappe.db.get_value("Synora Coach Claim", safe_claim_id, _CLAIM_FIELDS, as_dict=True)
    if not row:
        raise _not_available()
    if (
        str(row.run) != safe_run
        or str(row.initiator) != str(scope["initiator"])
        or str(row.company_scope) != str(scope["company"])
        or (str(row.warehouse_scope or "") or None) != (str(scope["warehouse"] or "") or None)
    ):
        raise _not_available()
    if expected_claim_digest is not None:
        try:
            if str(row.claim_digest) != _digest(expected_claim_digest, "claim_digest"):
                raise _not_available()
        except GatewayFault:
            raise _not_available() from None
    if expected_citation_digest is not None:
        try:
            if str(row.citation_digest) != _digest(expected_citation_digest, "citation_digest"):
                raise _not_available()
        except GatewayFault:
            raise _not_available() from None
    if source_revision is not None:
        if not isinstance(source_revision, str) or str(row.source_revision) != source_revision:
            raise _not_available()
    return _serialize(row)


__all__ = [
    "answer_contextual_coach",
    "persist_coach_claim",
    "persist_context_required_coach_result",
    "resolve_coach_claim",
    "serialize_coach_result",
    "validate_coach_capability",
]
