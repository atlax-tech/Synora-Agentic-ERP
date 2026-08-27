"""Frappe-side idempotency ledger for durable read-only workflow steps.

The ledger is deliberately separate from the Runtime checkpoint.  A checkpoint
describes orchestration progress; this table records whether one exact typed
Gateway invocation reached a durable result.  Only the Gateway boundary can
create or complete a row.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import frappe
from frappe.utils import now_datetime

from synora_agentic_erp.gateway.contract import GatewayFault, GatewayRequest
from synora_agentic_erp.gateway.registry import get_tool_spec
from synora_agentic_erp.gateway.security import RunContext, recheck_run_scope

MAX_RESPONSE_BYTES = 2_000_000


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise GatewayFault("INVALID_INPUT", "workflow invocation arguments are invalid") from error


def args_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def invocation_id(
    run_id: str,
    plan_version: int,
    step_id: str,
    tool_name: str,
    tool_version: str,
    digest: str,
) -> str:
    material = f"{run_id}|{plan_version}|{step_id}|{tool_name}|{tool_version}|{digest}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _bounded_response(value: object) -> tuple[dict[str, Any], str]:
    if not isinstance(value, dict):
        raise GatewayFault("ERP_ERROR", "tool returned an invalid result", 502)
    encoded = canonical_json(value).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise GatewayFault("RESULT_LIMIT", "tool result is too large", 502)
    # Round-trip makes the stored representation a JSON-safe object and rejects
    # accidental framework objects before they enter the ledger.
    try:
        parsed = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise GatewayFault("ERP_ERROR", "tool returned an invalid result", 502) from error
    if not isinstance(parsed, dict):
        raise GatewayFault("ERP_ERROR", "tool returned an invalid result", 502)
    return parsed, hashlib.sha256(encoded).hexdigest()


def _load_cached(doc: Any) -> dict[str, Any]:
    raw = doc.response_json or ""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise GatewayFault("ERP_ERROR", "cached tool result is invalid", 502)
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise GatewayFault("ERP_ERROR", "cached tool result is invalid", 502) from error
    response, digest = _bounded_response(value)
    if doc.observation_digest and str(doc.observation_digest) != digest:
        raise GatewayFault("ERP_ERROR", "cached tool result digest is invalid", 502)
    return response


@dataclass(frozen=True)
class InvocationReservation:
    invocation_id: str
    cached_response: dict[str, Any] | None = None


def _validate_workflow_metadata(request: GatewayRequest) -> tuple[str, int, str, str, str]:
    values = (request.invocation_id, request.plan_version, request.step_id, request.args_digest)
    if any(value is None for value in values):
        raise GatewayFault("INVALID_INPUT", "workflow invocation metadata is incomplete")
    assert request.invocation_id is not None
    assert request.plan_version is not None
    assert request.step_id is not None
    assert request.args_digest is not None
    computed = args_digest(request.tool.input)
    if computed != request.args_digest:
        raise GatewayFault("CONFLICT", "workflow invocation arguments conflict", 409)
    expected = invocation_id(
        request.run_id,
        request.plan_version,
        request.step_id,
        request.tool.name,
        request.tool.version,
        request.args_digest,
    )
    if expected != request.invocation_id:
        raise GatewayFault("CONFLICT", "workflow invocation identity conflicts", 409)
    return (
        request.invocation_id,
        request.plan_version,
        request.step_id,
        request.args_digest,
        computed,
    )


def _existing_reservation(
    doc: Any,
    *,
    expected_digest: str,
) -> InvocationReservation:
    if str(doc.args_digest or "") != expected_digest:
        raise GatewayFault("CONFLICT", "workflow invocation digest conflicts", 409)
    if doc.status == "SUCCEEDED":
        return InvocationReservation(str(doc.invocation_id), _load_cached(doc))
    # STARTED is intentionally not replayed: the previous process may have
    # reached ERP and crashed before publishing its durable result.
    raise GatewayFault("CONFLICT", "workflow tool result is uncertain", 409)


def reserve_invocation(
    request: GatewayRequest,
    run: RunContext,
) -> InvocationReservation | None:
    """Reserve an exact workflow call, or return its typed completed cache."""
    if request.invocation_id is None:
        if any(
            value is not None
            for value in (request.plan_version, request.step_id, request.args_digest)
        ):
            raise GatewayFault("INVALID_INPUT", "workflow invocation metadata is incomplete")
        return None

    identity, plan_version, step_id, digest, _ = _validate_workflow_metadata(request)
    spec = get_tool_spec(request.tool.name, request.tool.version)
    recheck_run_scope(run, spec.required_doctypes)
    existing_name = frappe.db.exists("Synora Workflow Tool Invocation", {"invocation_id": identity})
    if existing_name:
        return _existing_reservation(
            frappe.get_doc("Synora Workflow Tool Invocation", existing_name),
            expected_digest=digest,
        )

    values = {
        "doctype": "Synora Workflow Tool Invocation",
        "name": identity,
        "invocation_id": identity,
        "run": run.run_id,
        "initiator": run.initiator,
        "plan_version": plan_version,
        "step_id": step_id,
        "tool_name": request.tool.name,
        "tool_version": request.tool.version,
        "args_digest": digest,
        "status": "STARTED",
        "started_at": now_datetime(),
        "correlation_id": request.correlation_id,
    }
    try:
        frappe.get_doc(values).insert(ignore_permissions=True)
    except frappe.DuplicateEntryError, frappe.UniqueValidationError:
        # Another request won the race.  Re-read its immutable row rather than
        # attempting a second ERP call.
        existing_name = frappe.db.exists(
            "Synora Workflow Tool Invocation", {"invocation_id": identity}
        )
        if not existing_name:
            raise GatewayFault("CONFLICT", "workflow invocation reservation raced", 409) from None
        return _existing_reservation(
            frappe.get_doc("Synora Workflow Tool Invocation", existing_name),
            expected_digest=digest,
        )
    return InvocationReservation(identity)


def complete_invocation(
    reservation: InvocationReservation,
    response: object,
) -> dict[str, Any]:
    if reservation.cached_response is not None:
        return reservation.cached_response
    safe_response, observation_digest = _bounded_response(response)
    doc = frappe.get_doc("Synora Workflow Tool Invocation", reservation.invocation_id)
    if doc.status != "STARTED":
        if doc.status == "SUCCEEDED":
            return _load_cached(doc)
        raise GatewayFault("CONFLICT", "workflow tool result is uncertain", 409)
    doc.status = "SUCCEEDED"
    doc.response_json = canonical_json(safe_response)
    doc.observation_digest = observation_digest
    doc.completed_at = now_datetime()
    doc.flags.synora_invocation_completion = True
    doc.save(ignore_permissions=True)
    return safe_response
