"""Frappe persistence boundary for Phase 6 governance records.

Only this deterministic service may create governance facts.  It stores the
already-normalized contracts and uses a narrow row lock for action state CAS;
there is no generic document writer and no ERP business-document mutation here.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import frappe
from frappe.utils import now_datetime

from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.governance.contracts import (
    ApprovalDecision,
    ExecutionReceipt,
    PolicyDecision,
    ProposedAction,
    build_proposed_action,
    create_execution_receipt,
    parse_approval_decision,
    parse_policy_decision,
)
from synora_agentic_erp.governance.state import transition_state

SERVICE_FLAG = "synora_governance_service"
STATE_FLAG = "synora_action_state_change"
VERIFIED_RECEIPT_FLAG = "synora_verified_execution"
RECONCILIATION_RECEIPT_FLAG = "synora_execution_receipt_reconciliation"


def _authenticated_actor() -> str:
    actor = str(getattr(frappe.session, "user", "Guest") or "Guest")
    if actor == "Guest":
        raise GatewayFault("AUTHENTICATION_REQUIRED", "authenticated user required", 401)
    return actor


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _action_values(action: ProposedAction) -> dict[str, Any]:
    return {
        "doctype": "Synora Proposed Action",
        "name": action.action_id,
        "schema_version": action.schema_version,
        "action_type": action.action_type,
        "run": action.run_id,
        "action_id": action.action_id,
        "initiator": action.initiator,
        "payload_json": _canonical_json(action.payload),
        "evidence_refs_json": _canonical_json(list(action.evidence_refs)),
        "calculation_refs_json": _canonical_json(list(action.calculation_refs)),
        "risk_class": action.risk_class,
        "approval_class": action.approval_class,
        "snapshot_ref": action.snapshot_ref,
        "idempotency_key": action.idempotency_key,
        "expires_at": action.expires_at,
        "revalidation_rule": action.revalidation_rule,
        "proposal_digest": action.proposal_digest,
        "summary": action.summary,
        "state": "DRAFT",
        "state_version": 1,
        "state_reason": "created",
        "state_changed_at": str(now_datetime()),
        "state_changed_by": action.initiator,
        "correlation_id": action.correlation_id,
    }


def _insert(values: dict[str, Any]) -> Any:
    doc = frappe.get_doc(values)
    doc.flags[SERVICE_FLAG] = True
    try:
        return doc.insert(ignore_permissions=True)
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError) as error:
        raise GatewayFault("CONFLICT", "governance record already exists", 409) from error


def persist_proposed_action(value: ProposedAction | object) -> dict[str, Any]:
    """Persist one validated DRAFT action; never mutates an ERP business document."""
    action = value if isinstance(value, ProposedAction) else build_proposed_action(value)
    actor = _authenticated_actor()
    if actor != action.initiator:
        raise GatewayFault("PERMISSION_DENIED", "action initiator does not match session", 403)
    if not frappe.db.exists("Synora Agent Run", action.run_id):
        raise GatewayFault("RUN_REJECTED", "run is not available", 404)
    run = frappe.get_doc("Synora Agent Run", action.run_id)
    if run.initiator != action.initiator:
        raise GatewayFault("PERMISSION_DENIED", "action run initiator does not match", 403)
    if frappe.db.exists("Synora Proposed Action", {"idempotency_key": action.idempotency_key}):
        raise GatewayFault("CONFLICT", "idempotency key already exists", 409)
    doc = _insert(_action_values(action))
    return serialize_action(doc)


def _policy_values(decision: PolicyDecision) -> dict[str, Any]:
    return {
        "doctype": "Synora Policy Decision",
        "name": decision.decision_id,
        "decision_id": decision.decision_id,
        "action": decision.action_id,
        "proposal_digest": decision.proposal_digest,
        "actor": decision.actor,
        "identity_outcome": decision.checks["identity"],
        "scope_outcome": decision.checks["scope"],
        "permission_outcome": decision.checks["permission"],
        "deterministic_outcome": decision.checks["deterministic"],
        "workflow_policy_outcome": decision.checks["workflow_policy"],
        "matched_rule": decision.matched_rule,
        "rule_version": decision.rule_version,
        "outcome": decision.outcome,
        "reason": decision.reason,
        "snapshot_ref": decision.snapshot_ref,
        "expires_at": decision.expires_at,
        "decided_at": decision.decided_at,
        "correlation_id": decision.correlation_id,
    }


def persist_policy_decision(value: PolicyDecision | object) -> dict[str, Any]:
    decision = value if isinstance(value, PolicyDecision) else parse_policy_decision(value)
    actor = _authenticated_actor()
    if actor != decision.actor:
        raise GatewayFault("PERMISSION_DENIED", "policy actor does not match session", 403)
    action = _load_action(decision.action_id)
    if action.proposal_digest != decision.proposal_digest:
        raise GatewayFault("CONFLICT", "policy decision digest conflicts", 409)
    doc = _insert(_policy_values(decision))
    return serialize_policy_decision(doc)


def _approval_values(decision: ApprovalDecision) -> dict[str, Any]:
    return {
        "doctype": "Synora Approval Decision",
        "name": decision.decision_id,
        "decision_id": decision.decision_id,
        "action": decision.action_id,
        "proposal_digest": decision.proposal_digest,
        "actor": decision.actor,
        "decision": decision.decision,
        "matched_rule": decision.matched_rule,
        "snapshot_ref": decision.snapshot_ref,
        "expires_at": decision.expires_at,
        "reason": decision.reason,
        "decided_at": decision.decided_at,
        "correlation_id": decision.correlation_id,
    }


def persist_approval_decision(value: ApprovalDecision | object) -> dict[str, Any]:
    decision = value if isinstance(value, ApprovalDecision) else parse_approval_decision(value)
    actor = _authenticated_actor()
    if actor != decision.actor:
        raise GatewayFault("PERMISSION_DENIED", "approval actor does not match session", 403)
    action = _load_action(decision.action_id)
    if action.proposal_digest != decision.proposal_digest:
        raise GatewayFault("CONFLICT", "approval decision digest conflicts", 409)
    doc = _insert(_approval_values(decision))
    return serialize_approval_decision(doc)


def persist_execution_receipt(
    value: ExecutionReceipt | object,
    *,
    verified_execution: bool = False,
) -> dict[str, Any]:
    """Persist a failure/uncertain receipt or a verified execution receipt.

    The success flag is deliberately explicit and is not exposed through a
    whitelisted endpoint.  A caller cannot manufacture success from transport
    acknowledgement alone.
    """
    receipt = value if isinstance(value, ExecutionReceipt) else create_execution_receipt(value)
    actor = _authenticated_actor()
    if actor != receipt.executor:
        raise GatewayFault("PERMISSION_DENIED", "receipt executor does not match session", 403)
    if receipt.final_state in {"SUCCEEDED", "RECONCILED_SUCCESS"} and not verified_execution:
        raise GatewayFault("PERMISSION_DENIED", "verified execution evidence is required", 403)
    action = _load_action(receipt.action_id)
    if (
        action.run_id != receipt.run_id
        or action.initiator != receipt.initiator
        or action.idempotency_key != receipt.idempotency_key
        or action.proposal_digest != receipt.proposal_digest
    ):
        raise GatewayFault("CONFLICT", "receipt action identity conflicts", 409)
    values = {
        "doctype": "Synora Execution Receipt",
        "name": receipt.receipt_id,
        "receipt_id": receipt.receipt_id,
        "action": receipt.action_id,
        "run": receipt.run_id,
        "idempotency_key": receipt.idempotency_key,
        "initiator": receipt.initiator,
        "approver": receipt.approver,
        "executor": receipt.executor,
        "proposal_digest": receipt.proposal_digest,
        "target_doctype": receipt.target_doctype,
        "target_name": receipt.target_name,
        "verified_fields_json": _canonical_json(receipt.verified_fields),
        "response_category": receipt.response_category,
        "failure_category": receipt.failure_category,
        "final_state": receipt.final_state,
        "started_at": receipt.started_at,
        "completed_at": receipt.completed_at,
        "correlation_id": receipt.correlation_id,
        "reconciliation_evidence_json": _canonical_json(receipt.reconciliation_evidence or {}),
    }
    doc = frappe.get_doc(values)
    doc.flags[SERVICE_FLAG] = True
    doc.flags[VERIFIED_RECEIPT_FLAG] = verified_execution
    try:
        doc.insert(ignore_permissions=True)
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError) as error:
        raise GatewayFault("CONFLICT", "execution receipt already exists", 409) from error
    return serialize_receipt(doc)


def transition_execution_receipt(
    value: ExecutionReceipt | object,
) -> dict[str, Any]:
    """Apply one controlled reconciliation transition to an uncertain Receipt."""

    receipt = value if isinstance(value, ExecutionReceipt) else create_execution_receipt(value)
    actor = _authenticated_actor()
    if actor != receipt.executor:
        raise GatewayFault("PERMISSION_DENIED", "receipt executor does not match session", 403)
    if receipt.final_state not in {
        "RECONCILED_SUCCESS",
        "RECONCILED_FAILURE",
        "MANUAL_INTERVENTION",
    }:
        raise GatewayFault("CONFLICT", "receipt reconciliation state is invalid", 409)
    try:
        doc = frappe.get_doc("Synora Execution Receipt", receipt.receipt_id)
    except frappe.DoesNotExistError as error:
        raise GatewayFault("NOT_FOUND", "execution receipt is not available", 404) from error
    if doc.final_state != "RECONCILIATION_REQUIRED":
        raise GatewayFault("CONFLICT", "execution receipt is already finalized", 409)
    identity_fields = {
        "receipt_id": "receipt_id",
        "action": "action_id",
        "run": "run_id",
        "idempotency_key": "idempotency_key",
        "initiator": "initiator",
        "approver": "approver",
        "executor": "executor",
        "proposal_digest": "proposal_digest",
        "started_at": "started_at",
        "correlation_id": "correlation_id",
    }
    if any(
        str(getattr(doc, doc_field) or "") != str(getattr(receipt, receipt_field) or "")
        for doc_field, receipt_field in identity_fields.items()
    ):
        raise GatewayFault("CONFLICT", "receipt reconciliation identity conflicts", 409)
    doc.target_doctype = receipt.target_doctype
    doc.target_name = receipt.target_name
    doc.verified_fields_json = _canonical_json(receipt.verified_fields)
    doc.response_category = receipt.response_category
    doc.failure_category = receipt.failure_category
    doc.final_state = receipt.final_state
    doc.completed_at = receipt.completed_at
    doc.reconciliation_evidence_json = _canonical_json(receipt.reconciliation_evidence or {})
    doc.flags[SERVICE_FLAG] = True
    doc.flags[RECONCILIATION_RECEIPT_FLAG] = True
    doc.flags[VERIFIED_RECEIPT_FLAG] = receipt.final_state == "RECONCILED_SUCCESS"
    try:
        doc.save(ignore_permissions=True)
    except frappe.TimestampMismatchError as error:
        raise GatewayFault("CONFLICT", "execution receipt changed concurrently", 409) from error
    return serialize_receipt(doc)


def _load_action(action_id: str) -> ProposedAction:
    try:
        doc = frappe.get_doc("Synora Proposed Action", action_id)
    except frappe.DoesNotExistError as error:
        raise GatewayFault("NOT_FOUND", "governed action is not available", 404) from error
    try:
        return build_proposed_action(_action_dict(doc))
    except Exception as error:
        raise GatewayFault("CONFLICT", "governed action record is invalid", 409) from error


def _action_dict(doc: Any) -> dict[str, Any]:
    return {
        "schema_version": doc.schema_version,
        "action_type": doc.action_type,
        "run_id": doc.run,
        "action_id": doc.action_id,
        "initiator": doc.initiator,
        "payload": json.loads(doc.payload_json),
        "evidence_refs": json.loads(doc.evidence_refs_json),
        "calculation_refs": json.loads(doc.calculation_refs_json),
        "risk_class": doc.risk_class,
        "approval_class": doc.approval_class,
        "snapshot_ref": doc.snapshot_ref,
        "idempotency_key": doc.idempotency_key,
        "expires_at": doc.expires_at,
        "revalidation_rule": doc.revalidation_rule,
        "proposal_digest": doc.proposal_digest,
        "summary": doc.summary or "",
        "correlation_id": doc.correlation_id,
    }


def transition_action_state(
    action_id: str,
    target: str,
    *,
    expected_version: int,
    reason: str,
    correlation_id: str,
    approval_digest: str | None = None,
) -> dict[str, Any]:
    """CAS a single action row under a database lock; no ERP write is performed."""
    _authenticated_actor()
    rows = frappe.db.sql(
        """
        SELECT name, state, state_version, initiator, proposal_digest
        FROM `tabSynora Proposed Action`
        WHERE name = %s
        FOR UPDATE
        """,
        (action_id,),
        as_dict=True,
    )
    if not rows:
        raise GatewayFault("NOT_FOUND", "governed action is not available", 404)
    row = rows[0]
    actor = str(frappe.session.user)
    if target in {"APPROVED", "DECLINED"}:
        # No user, including System Manager, may turn an awaiting action into
        # a terminal approval state without a same-session, same-digest proof.
        # This keeps the low-level CAS primitive from becoming an approval
        # bypass when called outside the HTTP API.
        if not approval_digest or approval_digest != row.proposal_digest:
            raise GatewayFault("PERMISSION_DENIED", "approval proof is required", 403)
        policy = frappe.db.exists(
            "Synora Policy Decision",
            {
                "action": action_id,
                "proposal_digest": approval_digest,
                "outcome": "ALLOW",
            },
        )
        approval = frappe.db.exists(
            "Synora Approval Decision",
            {
                "action": action_id,
                "proposal_digest": approval_digest,
                "actor": actor,
                "decision": "ALLOW" if target == "APPROVED" else "DECLINE",
            },
        )
        if not policy or not approval:
            raise GatewayFault("PERMISSION_DENIED", "approval proof is not available", 403)
        if actor != row.initiator and "System Manager" not in frappe.get_roles(actor):
            action = _load_action(action_id)
            if action.approval_class == "INITIATOR_CONFIRMATION" and actor != action.initiator:
                raise GatewayFault("PERMISSION_DENIED", "initiator confirmation is required", 403)
            if action.approval_class == "INDEPENDENT_APPROVER" and actor == action.initiator:
                raise GatewayFault(
                    "PERMISSION_DENIED",
                    "independent approval requires a different user",
                    403,
                )
    elif actor != row.initiator and "System Manager" not in frappe.get_roles(actor):
        raise GatewayFault("PERMISSION_DENIED", "governed action is not available", 403)
    new_state, new_version = transition_state(
        str(row.state),
        target,
        state_version=int(row.state_version),
        expected_version=expected_version,
    )
    if not reason or len(reason) > 2_000:
        raise GatewayFault("INVALID_INPUT", "state transition reason is invalid")
    doc = frappe.get_doc("Synora Proposed Action", action_id)
    doc.state = new_state
    doc.state_version = new_version
    doc.state_reason = reason
    doc.state_changed_at = str(now_datetime())
    doc.state_changed_by = actor
    doc.flags[SERVICE_FLAG] = True
    doc.flags[STATE_FLAG] = True
    try:
        doc.save(ignore_permissions=True)
    except frappe.TimestampMismatchError as error:
        raise GatewayFault("CONFLICT", "governed action changed concurrently", 409) from error
    return serialize_action(doc, allowed_actor=actor)


def _read_authorized(doc: Any, *, allowed_actor: str | None = None) -> None:
    actor = _authenticated_actor()
    if (
        actor == doc.initiator
        or actor == allowed_actor
        or "System Manager" in frappe.get_roles(actor)
    ):
        return
    raise GatewayFault("PERMISSION_DENIED", "governed action is not available", 403)


def serialize_action(doc: Any, *, allowed_actor: str | None = None) -> dict[str, Any]:
    _read_authorized(doc, allowed_actor=allowed_actor)
    action = build_proposed_action(_action_dict(doc))
    result = action.to_dict()
    result.update(
        {
            "state": doc.state,
            "state_version": int(doc.state_version),
            "state_reason": doc.state_reason,
            "state_changed_at": doc.state_changed_at,
            "state_changed_by": doc.state_changed_by,
        }
    )
    return result


def serialize_policy_decision(doc: Any) -> dict[str, Any]:
    _read_action_owner(doc.action)
    return {
        "decision_id": doc.decision_id,
        "action_id": doc.action,
        "proposal_digest": doc.proposal_digest,
        "actor": doc.actor,
        "checks": {
            "identity": doc.identity_outcome,
            "scope": doc.scope_outcome,
            "permission": doc.permission_outcome,
            "deterministic": doc.deterministic_outcome,
            "workflow_policy": doc.workflow_policy_outcome,
        },
        "matched_rule": doc.matched_rule,
        "rule_version": doc.rule_version,
        "outcome": doc.outcome,
        "reason": doc.reason,
        "snapshot_ref": doc.snapshot_ref,
        "expires_at": doc.expires_at,
        "decided_at": doc.decided_at,
        "correlation_id": doc.correlation_id,
    }


def serialize_approval_decision(doc: Any) -> dict[str, Any]:
    # The approval actor may be distinct from the action initiator.  It is
    # still safe to expose the newly-created fact because the persistence
    # service has already bound ``doc.actor`` to the current session.
    _read_action_owner(doc.action, allowed_actor=str(doc.actor))
    return {
        "decision_id": doc.decision_id,
        "action_id": doc.action,
        "proposal_digest": doc.proposal_digest,
        "actor": doc.actor,
        "decision": doc.decision,
        "matched_rule": doc.matched_rule,
        "snapshot_ref": doc.snapshot_ref,
        "expires_at": doc.expires_at,
        "reason": doc.reason,
        "decided_at": doc.decided_at,
        "correlation_id": doc.correlation_id,
    }


def serialize_receipt(doc: Any) -> dict[str, Any]:
    _read_action_owner(doc.action)
    return {
        "receipt_id": doc.receipt_id,
        "action_id": doc.action,
        "run_id": doc.run,
        "idempotency_key": doc.idempotency_key,
        "initiator": doc.initiator,
        "approver": doc.approver or None,
        "executor": doc.executor,
        "proposal_digest": doc.proposal_digest,
        "target_doctype": doc.target_doctype or None,
        "target_name": doc.target_name or None,
        "verified_fields": json.loads(doc.verified_fields_json),
        "response_category": doc.response_category,
        "failure_category": doc.failure_category or None,
        "final_state": doc.final_state,
        "started_at": doc.started_at,
        "completed_at": doc.completed_at or None,
        "correlation_id": doc.correlation_id,
        "reconciliation_evidence": json.loads(doc.reconciliation_evidence_json),
    }


def _read_action_owner(action_id: str, *, allowed_actor: str | None = None) -> None:
    doc = frappe.get_doc("Synora Proposed Action", action_id)
    _read_authorized(doc, allowed_actor=allowed_actor)


def new_decision_id() -> str:
    return str(uuid4())


__all__ = [
    "persist_approval_decision",
    "persist_execution_receipt",
    "persist_policy_decision",
    "persist_proposed_action",
    "serialize_action",
    "serialize_approval_decision",
    "serialize_policy_decision",
    "serialize_receipt",
    "transition_action_state",
]
