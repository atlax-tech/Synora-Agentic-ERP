"""Strict, versioned contracts for the first governed ERP actions.

The module intentionally has no Frappe dependency.  It is the single parser and
canonicalization boundary used before a governance record is persisted.  JSON
is only a transport representation; callers receive normalized typed mappings
and cannot use display text to change the reviewed payload.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from synora_agentic_erp.gateway.contract import GatewayFault

SCHEMA_VERSION = "1"
ACTION_TYPES = frozenset({"CREATE_MR_DRAFT", "CREATE_PO_DRAFT"})
RISK_CLASSES = frozenset({"LOW", "MEDIUM", "HIGH"})
APPROVAL_CLASSES = frozenset({"INITIATOR_CONFIRMATION", "INDEPENDENT_APPROVER"})
DRAFT_APPROVAL_CLASS = "INITIATOR_CONFIRMATION"
REVALIDATION_RULE = "FULL_PRE_EXECUTE_RECHECK_V1"
TARGET_DOCTYPES = {
    "CREATE_MR_DRAFT": "Material Request",
    "CREATE_PO_DRAFT": "Purchase Order",
}
RECEIPT_STATES = frozenset(
    {
        "SUCCEEDED",
        "FAILED",
        "RECONCILIATION_REQUIRED",
        "RECONCILED_SUCCESS",
        "RECONCILED_FAILURE",
        "MANUAL_INTERVENTION",
    }
)
RESPONSE_CATEGORIES = frozenset(
    {
        "ERP_SUCCESS",
        "ERP_VALIDATION_ERROR",
        "ERP_PERMISSION_ERROR",
        "ERP_NOT_FOUND",
        "TIMEOUT",
        "UNCERTAIN_RESULT",
        "CONFLICT",
    }
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SENSITIVE_KEY = re.compile(r"(?i)(secret|password|token|authorization|cookie|capability)")


def _invalid(message: str, *, conflict: bool = False) -> GatewayFault:
    return GatewayFault(
        "CONFLICT" if conflict else "INVALID_INPUT", message, 409 if conflict else 400
    )


def _required_text(value: object, label: str, maximum: int = 140) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or not value.strip():
        raise _invalid(f"{label} is invalid")
    return value


def _uuid(value: object, label: str) -> str:
    text = _required_text(value, label, 36)
    try:
        parsed = UUID(text)
    except (ValueError, AttributeError) as error:
        raise _invalid(f"{label} is invalid") from error
    normalized = str(parsed)
    if text.lower() != normalized:
        raise _invalid(f"{label} is invalid")
    return normalized


def _digest(value: object, label: str) -> str:
    text = _required_text(value, label, 64)
    if not _DIGEST.fullmatch(text):
        raise _invalid(f"{label} is invalid")
    return text


def _timestamp(value: object, label: str) -> str:
    text = _required_text(value, label, 64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise _invalid(f"{label} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _invalid(f"{label} must include a timezone")
    normalized = parsed.astimezone(UTC).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _date(value: object, label: str) -> str:
    text = _required_text(value, label, 10)
    if not _DATE.fullmatch(text):
        raise _invalid(f"{label} is invalid")
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as error:
        raise _invalid(f"{label} is invalid") from error
    return text


def _decimal(value: object, label: str, *, minimum: Decimal) -> str:
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, (str, int)):
        raise _invalid(f"{label} is invalid")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise _invalid(f"{label} is invalid") from error
    if not number.is_finite() or number < minimum:
        raise _invalid(f"{label} is invalid")
    # Fixed-point output removes exponent notation and insignificant zeroes.
    normalized = format(number.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized


def _object(value: object, fields: set[str], required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid(f"{label} must be an object")
    unknown = set(value) - fields
    missing = required - set(value)
    if unknown or missing:
        raise _invalid(f"{label} fields are invalid")
    return value


def _list(value: object, label: str, *, maximum: int = 100) -> list[Any]:
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise _invalid(f"{label} is invalid")
    return value


def _ref_list(value: object, label: str) -> tuple[str, ...]:
    values = _list(value, label, maximum=128)
    refs = tuple(sorted({_required_text(item, f"{label} item", 256) for item in values}))
    if len(refs) != len(values):
        raise _invalid(f"{label} contains duplicate references")
    return refs


def _idempotency_key(value: object, label: str = "idempotency_key") -> str:
    text = _required_text(value, label, 128)
    if not _IDEMPOTENCY_KEY.fullmatch(text):
        raise _invalid(f"{label} is invalid")
    return text


def _parse_item(value: object, action_type: str, index: int) -> dict[str, Any]:
    if action_type == "CREATE_MR_DRAFT":
        fields = {"item_code", "qty", "uom", "schedule_date", "warehouse", "description"}
        required = {"item_code", "qty", "schedule_date", "warehouse"}
    else:
        fields = {
            "item_code",
            "qty",
            "uom",
            "rate",
            "schedule_date",
            "warehouse",
            "description",
            "material_request",
        }
        required = {"item_code", "qty", "uom", "rate", "schedule_date", "warehouse"}
    raw = _object(value, fields, required, f"payload.items[{index}]")
    parsed: dict[str, Any] = {
        "item_code": _required_text(raw["item_code"], "item_code"),
        "qty": _decimal(raw["qty"], "qty", minimum=Decimal("1e-18")),
        "schedule_date": _date(raw["schedule_date"], "schedule_date"),
        "warehouse": _required_text(raw["warehouse"], "warehouse"),
    }
    for optional in ("uom", "description", "material_request"):
        if optional in raw and raw[optional] is not None:
            maximum = 2_000 if optional == "description" else 140
            parsed[optional] = _required_text(raw[optional], optional, maximum)
    if action_type == "CREATE_PO_DRAFT":
        parsed["rate"] = _decimal(raw["rate"], "rate", minimum=Decimal("1e-18"))
    return parsed


def _parse_payload(action_type: str, value: object) -> dict[str, Any]:
    if action_type == "CREATE_MR_DRAFT":
        fields = {"company", "transaction_date", "material_request_type", "items"}
        required = fields
    elif action_type == "CREATE_PO_DRAFT":
        fields = {
            "company",
            "supplier",
            "transaction_date",
            "schedule_date",
            "currency",
            "buying_price_list",
            "items",
        }
        required = fields
    else:
        raise _invalid("action_type is invalid")
    raw = _object(value, fields, required, "payload")
    parsed: dict[str, Any] = {
        "company": _required_text(raw["company"], "company"),
        "transaction_date": _date(raw["transaction_date"], "transaction_date"),
        "items": [
            _parse_item(item, action_type, index)
            for index, item in enumerate(_list(raw["items"], "items"))
        ],
    }
    if action_type == "CREATE_MR_DRAFT":
        material_type = _required_text(raw["material_request_type"], "material_request_type", 40)
        if material_type not in {
            "Purchase",
            "Manufacture",
            "Material Transfer",
            "Material Issue",
            "Subcontracting",
        }:
            raise _invalid("material_request_type is invalid")
        parsed["material_request_type"] = material_type
    else:
        parsed.update(
            {
                "supplier": _required_text(raw["supplier"], "supplier"),
                "schedule_date": _date(raw["schedule_date"], "schedule_date"),
                "currency": _required_text(raw["currency"], "currency", 3).upper(),
                "buying_price_list": _required_text(raw["buying_price_list"], "buying_price_list"),
            }
        )
    return parsed


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_json_object(value: object) -> dict[str, Any]:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 2_000_000:
        raise _invalid("JSON object is invalid")
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise _invalid("JSON object is invalid") from error
    if not isinstance(parsed, dict):
        raise _invalid("JSON object is invalid")
    return parsed


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise _invalid("canonical JSON value is invalid") from error


@dataclass(frozen=True)
class ProposedAction:
    schema_version: str
    action_type: str
    run_id: str
    action_id: str
    initiator: str
    payload: dict[str, Any]
    evidence_refs: tuple[str, ...]
    calculation_refs: tuple[str, ...]
    risk_class: str
    approval_class: str
    snapshot_ref: str
    idempotency_key: str
    expires_at: str
    revalidation_rule: str
    proposal_digest: str
    summary: str
    correlation_id: str

    def to_dict(self, *, include_display: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "action_type": self.action_type,
            "run_id": self.run_id,
            "action_id": self.action_id,
            "initiator": self.initiator,
            "payload": self.payload,
            "evidence_refs": list(self.evidence_refs),
            "calculation_refs": list(self.calculation_refs),
            "risk_class": self.risk_class,
            "approval_class": self.approval_class,
            "snapshot_ref": self.snapshot_ref,
            "idempotency_key": self.idempotency_key,
            "expires_at": self.expires_at,
            "revalidation_rule": self.revalidation_rule,
            "proposal_digest": self.proposal_digest,
            "correlation_id": self.correlation_id,
        }
        if include_display:
            result["summary"] = self.summary
        return result


_ACTION_FIELDS = {
    "schema_version",
    "action_type",
    "run_id",
    "action_id",
    "initiator",
    "payload",
    "evidence_refs",
    "calculation_refs",
    "risk_class",
    "approval_class",
    "snapshot_ref",
    "idempotency_key",
    "expires_at",
    "revalidation_rule",
    "proposal_digest",
    "summary",
    "correlation_id",
}
_ACTION_REQUIRED = _ACTION_FIELDS - {"proposal_digest", "summary"}


def canonical_proposal_bytes(action: ProposedAction) -> bytes:
    """Return bytes for reviewed content; display text and correlation are excluded."""
    material = action.to_dict(include_display=False)
    material.pop("proposal_digest", None)
    material.pop("correlation_id", None)
    return _canonical_json(material)


def proposal_digest(action: ProposedAction) -> str:
    return hashlib.sha256(canonical_proposal_bytes(action)).hexdigest()


def build_proposed_action(value: object) -> ProposedAction:
    raw = (
        parse_json_object(value)
        if isinstance(value, str)
        else _object(value, _ACTION_FIELDS, _ACTION_REQUIRED, "proposed action")
    )
    unknown = set(raw) - _ACTION_FIELDS
    missing = _ACTION_REQUIRED - set(raw)
    if unknown or missing or raw.get("schema_version") != SCHEMA_VERSION:
        raise _invalid("proposed action fields or schema version are invalid")
    action_type = raw.get("action_type")
    if action_type not in ACTION_TYPES:
        raise _invalid("action_type is invalid")
    summary = "" if raw.get("summary") is None else _required_text(raw["summary"], "summary", 2_000)
    idempotency_key = _idempotency_key(raw["idempotency_key"])
    action = ProposedAction(
        schema_version=SCHEMA_VERSION,
        action_type=action_type,
        run_id=_uuid(raw["run_id"], "run_id"),
        action_id=_uuid(raw["action_id"], "action_id"),
        initiator=_required_text(raw["initiator"], "initiator", 140),
        payload=_parse_payload(action_type, raw["payload"]),
        evidence_refs=_ref_list(raw["evidence_refs"], "evidence_refs"),
        calculation_refs=_ref_list(raw["calculation_refs"], "calculation_refs"),
        risk_class=raw["risk_class"]
        if isinstance(raw["risk_class"], str) and raw["risk_class"] in RISK_CLASSES
        else "",
        approval_class=raw["approval_class"]
        if isinstance(raw["approval_class"], str) and raw["approval_class"] in APPROVAL_CLASSES
        else "",
        snapshot_ref=_required_text(raw["snapshot_ref"], "snapshot_ref", 256),
        idempotency_key=idempotency_key,
        expires_at=_timestamp(raw["expires_at"], "expires_at"),
        revalidation_rule=raw["revalidation_rule"],
        proposal_digest="",
        summary=summary,
        correlation_id=_uuid(raw["correlation_id"], "correlation_id"),
    )
    if action.risk_class not in RISK_CLASSES or action.approval_class not in APPROVAL_CLASSES:
        raise _invalid("risk_class or approval_class is invalid")
    # Independent approval is reserved for future Submit/P2P actions.  The
    # Phase 6 action allowlist contains Draft writes only, so accepting it here
    # would create an approval that the current executor state machine cannot
    # safely complete.
    if action.approval_class != DRAFT_APPROVAL_CLASS:
        raise _invalid("current Draft actions require initiator confirmation")
    if action.revalidation_rule != REVALIDATION_RULE:
        raise _invalid("revalidation_rule is invalid")
    computed = proposal_digest(action)
    supplied = raw.get("proposal_digest")
    if supplied is not None and _digest(supplied, "proposal_digest") != computed:
        raise _invalid("proposal_digest conflicts with typed content", conflict=True)
    return ProposedAction(**{**action.__dict__, "proposal_digest": computed})


@dataclass(frozen=True)
class PolicyDecision:
    decision_id: str
    action_id: str
    proposal_digest: str
    actor: str
    checks: dict[str, str]
    matched_rule: str
    rule_version: str
    outcome: str
    reason: str
    snapshot_ref: str
    expires_at: str
    decided_at: str
    correlation_id: str


_POLICY_FIELDS = {
    "decision_id",
    "action_id",
    "proposal_digest",
    "actor",
    "checks",
    "matched_rule",
    "rule_version",
    "outcome",
    "reason",
    "snapshot_ref",
    "expires_at",
    "decided_at",
    "correlation_id",
}
_CHECKS = {"identity", "scope", "permission", "deterministic", "workflow_policy"}
_CHECK_VALUES = {"PASS", "FAIL", "UNKNOWN"}


def parse_policy_decision(value: object) -> PolicyDecision:
    raw = _object(value, _POLICY_FIELDS, _POLICY_FIELDS, "policy decision")
    checks = _object(raw["checks"], _CHECKS, _CHECKS, "policy checks")
    parsed_checks = {key: checks[key] for key in sorted(_CHECKS)}
    if any(item not in _CHECK_VALUES for item in parsed_checks.values()):
        raise _invalid("policy check is invalid")
    outcome = raw["outcome"]
    if outcome not in {"ALLOW", "REJECT"} or (
        outcome == "ALLOW" and any(v != "PASS" for v in parsed_checks.values())
    ):
        raise _invalid("policy outcome is invalid")
    return PolicyDecision(
        decision_id=_uuid(raw["decision_id"], "decision_id"),
        action_id=_uuid(raw["action_id"], "action_id"),
        proposal_digest=_digest(raw["proposal_digest"], "proposal_digest"),
        actor=_required_text(raw["actor"], "actor"),
        checks=parsed_checks,
        matched_rule=_required_text(raw["matched_rule"], "matched_rule", 256),
        rule_version=_required_text(raw["rule_version"], "rule_version", 64),
        outcome=outcome,
        reason=_required_text(raw["reason"], "reason", 2_000),
        snapshot_ref=_required_text(raw["snapshot_ref"], "snapshot_ref", 256),
        expires_at=_timestamp(raw["expires_at"], "expires_at"),
        decided_at=_timestamp(raw["decided_at"], "decided_at"),
        correlation_id=_uuid(raw["correlation_id"], "correlation_id"),
    )


@dataclass(frozen=True)
class ApprovalDecision:
    decision_id: str
    action_id: str
    proposal_digest: str
    actor: str
    decision: str
    matched_rule: str
    snapshot_ref: str
    expires_at: str
    reason: str
    decided_at: str
    correlation_id: str


_APPROVAL_FIELDS = {
    "decision_id",
    "action_id",
    "proposal_digest",
    "actor",
    "decision",
    "matched_rule",
    "snapshot_ref",
    "expires_at",
    "reason",
    "decided_at",
    "correlation_id",
}


def parse_approval_decision(value: object) -> ApprovalDecision:
    raw = _object(value, _APPROVAL_FIELDS, _APPROVAL_FIELDS, "approval decision")
    decision = raw["decision"]
    if decision not in {"ALLOW", "DECLINE", "CHANGES_REQUESTED"}:
        raise _invalid("approval decision is invalid")
    return ApprovalDecision(
        decision_id=_uuid(raw["decision_id"], "decision_id"),
        action_id=_uuid(raw["action_id"], "action_id"),
        proposal_digest=_digest(raw["proposal_digest"], "proposal_digest"),
        actor=_required_text(raw["actor"], "actor"),
        decision=decision,
        matched_rule=_required_text(raw["matched_rule"], "matched_rule", 256),
        snapshot_ref=_required_text(raw["snapshot_ref"], "snapshot_ref", 256),
        expires_at=_timestamp(raw["expires_at"], "expires_at"),
        reason=_required_text(raw["reason"], "reason", 2_000),
        decided_at=_timestamp(raw["decided_at"], "decided_at"),
        correlation_id=_uuid(raw["correlation_id"], "correlation_id"),
    )


@dataclass(frozen=True)
class ExecutionReceipt:
    receipt_id: str
    action_id: str
    run_id: str
    idempotency_key: str
    initiator: str
    approver: str | None
    executor: str
    proposal_digest: str
    target_doctype: str | None
    target_name: str | None
    verified_fields: dict[str, Any]
    response_category: str
    failure_category: str | None
    final_state: str
    started_at: str
    completed_at: str | None
    correlation_id: str
    reconciliation_evidence: dict[str, Any] | None


_RECEIPT_FIELDS = {
    "receipt_id",
    "action_id",
    "run_id",
    "idempotency_key",
    "initiator",
    "approver",
    "executor",
    "proposal_digest",
    "target_doctype",
    "target_name",
    "verified_fields",
    "response_category",
    "failure_category",
    "final_state",
    "started_at",
    "completed_at",
    "correlation_id",
    "reconciliation_evidence",
}
_RECEIPT_REQUIRED = _RECEIPT_FIELDS - {"approver", "completed_at", "reconciliation_evidence"}


def _safe_fields(value: object, label: str, *, required: bool) -> dict[str, Any]:
    if value is None and not required:
        return {}
    if not isinstance(value, dict) or (required and not value) or len(value) > 64:
        raise _invalid(f"{label} is invalid")
    parsed: dict[str, Any] = {}
    for key, item in value.items():
        safe_key = _required_text(key, f"{label} key", 80)
        if _SENSITIVE_KEY.search(safe_key):
            raise _invalid(f"{label} contains a sensitive key")
        if not isinstance(item, (str, int, float, bool)) and item is not None:
            raise _invalid(f"{label} contains an invalid value")
        if isinstance(item, float) and not (item == item and abs(item) != float("inf")):
            raise _invalid(f"{label} contains a non-finite value")
        parsed[safe_key] = item
    return parsed


def create_execution_receipt(value: object) -> ExecutionReceipt:
    raw = _object(value, _RECEIPT_FIELDS, _RECEIPT_REQUIRED, "execution receipt")
    final_state = raw["final_state"]
    response_category = raw["response_category"]
    if final_state not in RECEIPT_STATES or response_category not in RESPONSE_CATEGORIES:
        raise _invalid("receipt state or response category is invalid")
    target_doctype = raw["target_doctype"]
    target_name = raw["target_name"]
    if target_doctype is not None and target_doctype not in {"Material Request", "Purchase Order"}:
        raise _invalid("receipt target doctype is invalid")
    if target_name is not None:
        target_name = _required_text(target_name, "target_name", 140)
    if (target_doctype is None and target_name is not None) or (
        target_doctype is not None and target_name is None
    ):
        raise _invalid("receipt target identity is incomplete")
    verified = _safe_fields(
        raw["verified_fields"],
        "verified_fields",
        required=final_state in {"SUCCEEDED", "RECONCILED_SUCCESS"},
    )
    if final_state in {"SUCCEEDED", "RECONCILED_SUCCESS"}:
        if (
            not target_doctype
            or not target_name
            or not verified
            or response_category != "ERP_SUCCESS"
        ):
            raise _invalid("successful receipt requires verified ERP outcome")
        if raw["failure_category"] is not None:
            raise _invalid("successful receipt cannot contain failure category")
    failure = raw["failure_category"]
    if failure is not None:
        failure = _required_text(failure, "failure_category", 80)
    completed = (
        None if raw.get("completed_at") is None else _timestamp(raw["completed_at"], "completed_at")
    )
    reconciliation = None
    if raw.get("reconciliation_evidence") is not None:
        reconciliation = _safe_fields(
            raw["reconciliation_evidence"], "reconciliation_evidence", required=False
        )
    return ExecutionReceipt(
        receipt_id=_uuid(raw["receipt_id"], "receipt_id"),
        action_id=_uuid(raw["action_id"], "action_id"),
        run_id=_uuid(raw["run_id"], "run_id"),
        idempotency_key=_idempotency_key(raw["idempotency_key"]),
        initiator=_required_text(raw["initiator"], "initiator"),
        approver=None
        if raw.get("approver") is None
        else _required_text(raw["approver"], "approver"),
        executor=_required_text(raw["executor"], "executor"),
        proposal_digest=_digest(raw["proposal_digest"], "proposal_digest"),
        target_doctype=target_doctype,
        target_name=target_name,
        verified_fields=verified,
        response_category=response_category,
        failure_category=failure,
        final_state=final_state,
        started_at=_timestamp(raw["started_at"], "started_at"),
        completed_at=completed,
        correlation_id=_uuid(raw["correlation_id"], "correlation_id"),
        reconciliation_evidence=reconciliation,
    )
