"""Frappe authority for validated, durable Coach claim provenance."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from collections.abc import Mapping
from typing import Any

import frappe

from synora_agentic_erp.gateway.contract import GatewayFault, canonical_uuid
from synora_agentic_erp.memory.service import _actor, _run_scope
from synora_agentic_erp.synora_agentic_erp.doctype.synora_coach_claim.synora_coach_claim import (
    MAX_SOURCE_SNAPSHOT_LENGTH,
    SERVICE_FLAG,
)

MAX_CLAIM_LENGTH = 4_000
MAX_REVISION_LENGTH = 140
_RUNTIME_TOKEN_ENV = "SYNORA_RUNTIME_TOKEN"
_CLAIM_HMAC_DOMAIN = b"synora-coach-claim-v1"
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CLAIM_TYPES = frozenset({"ERP_FACT", "RETRIEVED_KNOWLEDGE", "RECOMMENDATION"})
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


__all__ = ["persist_coach_claim", "resolve_coach_claim"]
