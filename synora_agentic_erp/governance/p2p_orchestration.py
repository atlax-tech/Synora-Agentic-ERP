"""Durable P2P PlanStep projection and Run-level orchestration guards.

The ERP documents, governed Actions, Reservations and Receipts remain the
business facts.  This small projection stores dependency edges and a bounded
step state so a Run can resume without replaying a completed side effect.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import frappe
from frappe.utils import now_datetime

from synora_agentic_erp.agent.service import _set_run_state
from synora_agentic_erp.agent.state_machine import validate_transition
from synora_agentic_erp.gateway.contract import GatewayFault
from synora_agentic_erp.gateway.security import workflow_expired
from synora_agentic_erp.governance.contracts import (
    P2P_ACTION_TYPES,
    TARGET_DOCTYPES,
    build_proposed_action,
)

P2P_STEP_SCHEMA_VERSION = "1"
P2P_STEP_SERVICE_FLAG = "synora_p2p_orchestration_service"
SUCCESS_RECEIPT_STATES = frozenset({"SUCCEEDED", "RECONCILED_SUCCESS"})
UNCERTAIN_RECEIPT_STATES = frozenset({"RECONCILIATION_REQUIRED", "MANUAL_INTERVENTION"})
FAILED_RECEIPT_STATES = frozenset({"FAILED", "RECONCILED_FAILURE"})
TERMINAL_STEP_STATES = frozenset(
    {
        "SUCCEEDED",
        "FAILED",
        "RECONCILIATION_REQUIRED",
        "REINVESTIGATION_REQUIRED",
        "CANCELLED",
        "EXPIRED",
    }
)
ACTION_ORDER = {
    "SUBMIT_PO": 10,
    "CREATE_PR_DRAFT": 20,
    "SUBMIT_PR": 30,
    "CREATE_PI_DRAFT": 40,
    "SUBMIT_PI": 50,
    "CREATE_PAYMENT_ENTRY_DRAFT": 60,
    "SUBMIT_PAYMENT_ENTRY": 70,
    "CANCEL_PAYMENT_ENTRY": 80,
    "CANCEL_PI": 90,
    "CANCEL_PR": 100,
    "CANCEL_PO": 110,
}


@dataclass(frozen=True)
class P2PPlanStepView:
    action_id: str
    step_id: str
    order: int
    state: str
    depends_on: tuple[str, ...]
    blocked_reason: str | None = None
    reinvestigation_required: bool = False


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _stable_refs(value: Iterable[tuple[str, str]] | None) -> list[list[str]]:
    """Turn internal reference tuples/sets into a deterministic JSON value."""

    return [list(ref) for ref in sorted(value or set())]


def _action_from_doc(doc: Any) -> Any:
    try:
        return build_proposed_action(
            {
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
        )
    except Exception as error:
        raise GatewayFault("CONFLICT", "governed action record is invalid", 409) from error


def _target_ref(
    action: Any, receipt: Any | None = None, reservation: dict[str, Any] | None = None
) -> tuple[str, str] | None:
    if (
        receipt is not None
        and getattr(receipt, "target_doctype", None)
        and getattr(receipt, "target_name", None)
    ):
        return str(receipt.target_doctype), str(receipt.target_name)
    if reservation and reservation.get("target_doctype") and reservation.get("target_name"):
        return str(reservation["target_doctype"]), str(reservation["target_name"])
    payload = action.payload
    source_doctype = str(payload.get("source_doctype") or "")
    source_name = str(payload.get("source_name") or "")
    return (source_doctype, source_name) if source_doctype and source_name else None


def _related_refs(action: Any, receipt: Any | None = None) -> set[tuple[str, str]]:
    refs: set[tuple[str, str]] = set()
    payload = action.payload
    source = _target_ref(action, receipt)
    if source:
        refs.add(source)
    source_doctype = str(payload.get("source_doctype") or "")
    source_name = str(payload.get("source_name") or "")
    if source_doctype and source_name:
        refs.add((source_doctype, source_name))
    for reference in payload.get("references", []) or []:
        if isinstance(reference, dict):
            doctype = str(reference.get("reference_doctype") or "")
            name = str(reference.get("reference_name") or "")
            if doctype and name:
                refs.add((doctype, name))
    if receipt is not None:
        try:
            verified = json.loads(receipt.verified_fields_json or "{}")
        except (TypeError, ValueError):  # fmt: skip
            verified = {}
        if isinstance(verified, dict):
            for key, value in verified.items():
                if (
                    key.endswith((".purchase_order", ".purchase_receipt", ".reference_name"))
                    and value
                ):
                    if key.endswith("purchase_order"):
                        doctype = "Purchase Order"
                    elif key.endswith("purchase_receipt"):
                        doctype = "Purchase Receipt"
                    else:
                        doctype = "Purchase Invoice"
                    refs.add((doctype, str(value)))
    return refs


def _minimal_entry(
    doc: Any, receipt: Any | None = None, reservation: dict[str, Any] | None = None
) -> dict[str, Any]:
    action = _action_from_doc(doc)
    return {
        "action": action,
        "action_id": str(action.action_id),
        "action_type": str(action.action_type),
        "run_id": str(action.run_id),
        "state": str(doc.state),
        "state_reason": str(doc.state_reason or ""),
        "receipt": receipt,
        "target_ref": _target_ref(action, receipt, reservation),
        "related_refs": _related_refs(action, receipt),
        "reservation": reservation,
        "created_at": str(getattr(doc, "creation", "") or ""),
    }


def _entries_for_run(run_id: str) -> list[dict[str, Any]]:
    rows = frappe.get_all(
        "Synora Proposed Action",
        filters={"run": run_id, "action_type": ["in", sorted(P2P_ACTION_TYPES)]},
        fields=["name"],
        order_by="creation asc, name asc",
        limit_page_length=200,
        ignore_permissions=True,
    )
    entries: list[dict[str, Any]] = []
    for row in rows:
        receipt_rows = frappe.get_all(
            "Synora Execution Receipt",
            filters={"action": row.name},
            fields=["name"],
            order_by="completed_at desc, creation desc",
            limit_page_length=1,
            ignore_permissions=True,
        )
        receipt = (
            frappe.get_doc("Synora Execution Receipt", receipt_rows[0].name)
            if receipt_rows
            else None
        )
        reservation_rows = frappe.get_all(
            "Synora Execution Reservation",
            filters={"action": row.name},
            fields=[
                "reservation_id",
                "status",
                "target_doctype",
                "target_name",
                "executor",
                "receipt",
                "response_category",
                "failure_category",
                "lease_expires_at",
            ],
            order_by="creation desc",
            limit_page_length=1,
            ignore_permissions=True,
        )
        reservation = reservation_rows[0] if reservation_rows else None
        entries.append(
            _minimal_entry(frappe.get_doc("Synora Proposed Action", row.name), receipt, reservation)
        )
    return entries


def infer_dependencies(entries: Iterable[dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    """Infer only same-Run, same-document edges from persisted facts.

    This is deliberately bounded O(n²); a Run is capped at 200 actions and a
    database-backed PlanStep keeps the graph stable across process restarts.
    """

    ordered = sorted(
        (entry for entry in entries if entry.get("action_type") in ACTION_ORDER),
        key=lambda entry: (
            str(entry.get("created_at") or ""),
            str(entry.get("action_id") or ""),
        ),
    )
    result: dict[str, tuple[str, ...]] = {}
    for index, current in enumerate(ordered):
        rank = ACTION_ORDER[str(current["action_type"])]
        current_refs = set(current.get("related_refs") or ())
        candidates: list[tuple[str, int, str]] = []
        for prior in ordered[:index]:
            prior_rank = ACTION_ORDER.get(str(prior["action_type"]))
            if prior_rank is None or prior_rank >= rank:
                continue
            prior_refs = set(prior.get("related_refs") or ())
            if current_refs and current_refs.intersection(prior_refs):
                candidates.append(
                    (
                        str(prior["action_id"]),
                        prior_rank,
                        str(prior.get("created_at") or ""),
                    )
                )
        # Keep every direct stage edge when documents share a source; this
        # preserves parallel partial actions while preventing a skipped write.
        result[str(current["action_id"])] = tuple(
            action_id
            for action_id, _rank, _created in sorted(
                candidates,
                key=lambda value: (value[1], value[2], value[0]),
            )
        )
    return result


def _receipt_state(entry: dict[str, Any]) -> str | None:
    receipt = entry.get("receipt")
    if receipt is None:
        return None
    return str(getattr(receipt, "final_state", "") or "")


def derive_step_views(entries: Iterable[dict[str, Any]]) -> tuple[P2PPlanStepView, ...]:
    """Project Action/Approval/Reservation/Receipt facts into PlanStep views."""

    ordered = sorted(
        entries,
        key=lambda entry: (
            str(entry.get("created_at") or ""),
            str(entry.get("action_id") or ""),
        ),
    )
    dependencies = infer_dependencies(ordered)
    raw: dict[str, str] = {}
    for entry in ordered:
        action_id = str(entry["action_id"])
        state = str(entry.get("state") or "DRAFT")
        receipt_state = _receipt_state(entry)
        reservation = entry.get("reservation") or {}
        # An EXECUTED Action without a durable success Receipt is ambiguous;
        # it must remain recoverable and cannot make the Run appear complete.
        if receipt_state in SUCCESS_RECEIPT_STATES:
            projected = "SUCCEEDED"
        elif (
            receipt_state in FAILED_RECEIPT_STATES
            or str(reservation.get("status") or "") in FAILED_RECEIPT_STATES
            or state == "POLICY_REJECTED"
        ):
            projected = "FAILED"
        elif (
            receipt_state in UNCERTAIN_RECEIPT_STATES
            or str(reservation.get("status") or "") in UNCERTAIN_RECEIPT_STATES
        ):
            projected = "RECONCILIATION_REQUIRED"
        elif str(reservation.get("status") or "") == "STARTED":
            projected = "EXECUTING"
        elif state == "EXECUTED":
            projected = "RECONCILIATION_REQUIRED"
        elif state == "AWAITING_APPROVAL":
            projected = "WAITING_APPROVAL"
        elif state == "APPROVED":
            projected = "READY"
        elif state in {"DECLINED", "EXPIRED"}:
            projected = state
        else:
            projected = "PLANNED"
        raw[action_id] = projected

    views: list[P2PPlanStepView] = []
    for order, entry in enumerate(ordered, 1):
        action_id = str(entry["action_id"])
        deps = dependencies.get(action_id, ())
        projected = raw[action_id]
        blocked_reason: str | None = None
        if projected not in TERMINAL_STEP_STATES:
            dependency_states = [(dep, raw.get(dep, "BLOCKED")) for dep in deps]
            failed = next(
                (
                    item
                    for item in dependency_states
                    if item[1]
                    in {
                        "FAILED",
                        "RECONCILIATION_REQUIRED",
                        "REINVESTIGATION_REQUIRED",
                        "CANCELLED",
                        "EXPIRED",
                    }
                ),
                None,
            )
            pending = next(
                (item for item in dependency_states if item[1] != "SUCCEEDED"),
                None,
            )
            if failed:
                projected = "BLOCKED"
                blocked_reason = f"dependency {failed[0]} is {failed[1]}"
            elif pending:
                projected = "WAITING_DEPENDENCY"
                blocked_reason = f"waiting for dependency {pending[0]}"
        views.append(
            P2PPlanStepView(
                action_id=action_id,
                step_id=f"p2p-{action_id[:12]}",
                order=order,
                state=projected,
                depends_on=deps,
                blocked_reason=blocked_reason,
                reinvestigation_required=bool(entry.get("reinvestigation_required", False)),
            )
        )
    return tuple(views)


def _overall_status(run_state: str, views: tuple[P2PPlanStepView, ...]) -> str:
    if run_state in {"CANCELLED", "EXPIRED"}:
        return run_state
    if run_state in {"FAILED", "SUCCEEDED"} and not views:
        return run_state
    if any(
        view.state == "REINVESTIGATION_REQUIRED" or view.reinvestigation_required for view in views
    ):
        return "REINVESTIGATION_REQUIRED"
    if any(view.state == "RECONCILIATION_REQUIRED" for view in views):
        return "RECONCILIATION_REQUIRED"
    if any(view.state == "BLOCKED" for view in views):
        return "BLOCKED"
    if any(view.state == "FAILED" for view in views):
        return "FAILED"
    if views and all(view.state == "SUCCEEDED" for view in views):
        return "SUCCEEDED"
    if any(view.state == "WAITING_APPROVAL" for view in views):
        return "WAITING_APPROVAL"
    if any(view.state == "WAITING_DEPENDENCY" for view in views):
        return "WAITING_DEPENDENCY"
    if any(view.state == "EXECUTING" for view in views):
        return "EXECUTING"
    return "PLANNED" if not views else "IN_PROGRESS"


def chain_from_entries(run_state: str, entries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    views = derive_step_views(entries)
    entries_by_id = {str(entry["action_id"]): entry for entry in entries}
    timeline: list[dict[str, Any]] = []
    for view in views:
        entry = entries_by_id[view.action_id]
        action = entry["action"]
        timeline.append(
            {
                "step_id": view.step_id,
                "action_id": view.action_id,
                "action_type": str(action.action_type),
                "state": view.state,
                "target_doctype": TARGET_DOCTYPES.get(str(action.action_type)),
                "target_name": (entry.get("target_ref") or (None, None))[1],
                "depends_on": list(view.depends_on),
                "blocked_reason": view.blocked_reason,
                "receipt_state": _receipt_state(entry),
            }
        )
    status = _overall_status(run_state, views)
    blocked_reasons = [view.blocked_reason for view in views if view.blocked_reason]
    completion_ready = bool(views) and all(view.state == "SUCCEEDED" for view in views)
    next_step = next(
        (
            view.step_id
            for view in views
            if view.state in {"WAITING_APPROVAL", "READY", "EXECUTING", "WAITING_DEPENDENCY"}
        ),
        None,
    )
    return {
        "schema_version": P2P_STEP_SCHEMA_VERSION,
        "status": status,
        "completion_ready": completion_ready,
        "needs_reinvestigation": status == "REINVESTIGATION_REQUIRED",
        "next_step_id": next_step,
        "blocked_reasons": blocked_reasons,
        "steps": [
            {
                "step_id": view.step_id,
                "action_id": view.action_id,
                "order": view.order,
                "state": view.state,
                "depends_on": list(view.depends_on),
                "blocked_reason": view.blocked_reason,
                "reinvestigation_required": view.reinvestigation_required,
            }
            for view in views
        ],
        "timeline": timeline,
    }


def _ensure_step_doc(entry: dict[str, Any], view: P2PPlanStepView) -> Any:
    action = entry["action"]
    existing = frappe.db.get_value("Synora P2P Plan Step", {"action": view.action_id}, "name")
    if existing:
        return frappe.get_doc("Synora P2P Plan Step", existing)
    source = action.payload
    doc = frappe.get_doc(
        {
            "doctype": "Synora P2P Plan Step",
            "run": action.run_id,
            "action": view.action_id,
            "step_id": view.step_id,
            "step_order": view.order,
            "depends_on_json": _canonical_json(list(view.depends_on)),
            "state": view.state,
            "target_doctype": TARGET_DOCTYPES.get(action.action_type),
            "target_name": (entry.get("target_ref") or (None, None))[1],
            "source_doctype": source.get("source_doctype"),
            "source_name": source.get("source_name"),
            "blocked_reason": view.blocked_reason,
            "reinvestigation_required": int(view.reinvestigation_required),
            "observed_digest": hashlib.sha256(
                _canonical_json(_stable_refs(entry.get("related_refs"))).encode()
            ).hexdigest(),
            "correlation_id": action.correlation_id,
            "last_evaluated_at": now_datetime(),
        }
    )
    doc.flags[P2P_STEP_SERVICE_FLAG] = True
    doc.insert(ignore_permissions=True)
    return doc


def sync_p2p_plan_steps(run_id: str) -> dict[str, Any]:
    entries = _entries_for_run(run_id)
    chain = chain_from_entries(
        str(frappe.db.get_value("Synora Agent Run", run_id, "run_state") or ""), entries
    )
    views = derive_step_views(entries)
    for entry, view in zip(
        sorted(
            entries,
            key=lambda value: (
                str(value.get("created_at") or ""),
                str(value.get("action_id") or ""),
            ),
        ),
        views,
        strict=True,
    ):
        doc = _ensure_step_doc(entry, view)
        doc.depends_on_json = _canonical_json(list(view.depends_on))
        doc.state = view.state
        doc.target_doctype = TARGET_DOCTYPES.get(entry["action_type"])
        doc.target_name = (entry.get("target_ref") or (None, None))[1]
        doc.source_doctype = entry["action"].payload.get("source_doctype")
        doc.source_name = entry["action"].payload.get("source_name")
        doc.blocked_reason = view.blocked_reason
        doc.reinvestigation_required = int(view.reinvestigation_required)
        doc.observed_digest = hashlib.sha256(
            _canonical_json(_stable_refs(entry.get("related_refs"))).encode()
        ).hexdigest()
        doc.last_evaluated_at = now_datetime()
        doc.flags[P2P_STEP_SERVICE_FLAG] = True
        doc.save(ignore_permissions=True)
    return chain


def ensure_p2p_plan_step(action: Any) -> dict[str, Any]:
    sync_p2p_plan_steps(str(action.run_id))
    return get_p2p_chain(str(action.run_id))


def get_p2p_chain(
    run_id: str, *, entries: Iterable[dict[str, Any]] | None = None, run_state: str | None = None
) -> dict[str, Any]:
    resolved_entries = list(entries) if entries is not None else _entries_for_run(run_id)
    state = run_state or str(frappe.db.get_value("Synora Agent Run", run_id, "run_state") or "")
    chain = chain_from_entries(state, resolved_entries)
    stored = frappe.get_all(
        "Synora P2P Plan Step",
        filters={"run": run_id},
        fields=["action", "state", "depends_on_json", "blocked_reason", "reinvestigation_required"],
        order_by="step_order asc",
        limit_page_length=200,
        ignore_permissions=True,
    )
    if stored:
        by_action = {str(row.action): row for row in stored}
        for step in chain["steps"]:
            row = by_action.get(str(step["action_id"]))
            if row:
                try:
                    dependencies = json.loads(row.depends_on_json or "[]")
                except (TypeError, ValueError):  # fmt: skip
                    dependencies = step["depends_on"]
                if isinstance(dependencies, list):
                    step["depends_on"] = dependencies
                step["stored_state"] = str(row.state)
                step["reinvestigation_required"] = bool(row.reinvestigation_required)
                if row.blocked_reason:
                    step["blocked_reason"] = str(row.blocked_reason)
    return chain


def assert_p2p_action_dependencies(action_id: str) -> None:
    entries = _entries_for_run(
        str(frappe.db.get_value("Synora Proposed Action", action_id, "run") or "")
    )
    current = next((entry for entry in entries if entry["action_id"] == action_id), None)
    if current is None:
        raise GatewayFault("NOT_FOUND", "governed action is not available", 404)
    dependencies = infer_dependencies(entries).get(action_id, ())
    by_id = {entry["action_id"]: entry for entry in entries}
    for dependency_id in dependencies:
        dependency = by_id.get(dependency_id)
        if dependency is None:
            raise GatewayFault("CONFLICT", "P2P action dependency is unavailable", 409)
        receipt_state = _receipt_state(dependency)
        if (
            str(dependency.get("state")) != "EXECUTED"
            or receipt_state not in SUCCESS_RECEIPT_STATES
        ):
            reason = str(dependency.get("state_reason") or receipt_state or "not confirmed")
            raise GatewayFault(
                "CONFLICT",
                f"P2P action dependency is not confirmed: {dependency_id} ({reason})",
                409,
            )


def live_reinvestigation(run_id: str, actor: str) -> dict[str, Any]:
    """Read current target facts and flag drift without invoking any writer."""

    entries = _entries_for_run(run_id)
    drift: list[dict[str, str]] = []
    for entry in entries:
        receipt = entry.get("receipt")
        if (
            receipt is None
            or str(getattr(receipt, "final_state", "")) not in SUCCESS_RECEIPT_STATES
        ):
            continue
        target_name = str(getattr(receipt, "target_name", "") or "")
        target_doctype = str(getattr(receipt, "target_doctype", "") or "")
        if not target_name or not target_doctype:
            continue
        if not frappe.has_permission(target_doctype, "read", user=actor):
            drift.append(
                {
                    "action_id": entry["action_id"],
                    "reason": "current target read permission is unavailable",
                }
            )
            continue
        try:
            target = frappe.get_doc(target_doctype, target_name)
            from synora_agentic_erp.governance.execution_contracts import (
                p2p_receipt_evidence_matches,
            )
            from synora_agentic_erp.governance.p2p_execution import p2p_read_back_with_financials

            current = p2p_read_back_with_financials(entry["action"], target)
            recorded = json.loads(receipt.verified_fields_json or "{}")
            if not p2p_receipt_evidence_matches(entry["action"], recorded, current):
                drift.append(
                    {
                        "action_id": entry["action_id"],
                        "reason": "ERP target no longer matches the saved Receipt",
                    }
                )
        except Exception as error:
            drift.append({"action_id": entry["action_id"], "reason": str(error)[:240]})
    return {"drift": drift, "needs_reinvestigation": bool(drift)}


def _authorized_run(run_id: str, *, lock: bool = False) -> Any:
    if not frappe.db.exists("Synora Agent Run", run_id):
        raise GatewayFault("RUN_REJECTED", "run is not available", 404)
    if lock:
        locked = frappe.db.sql(
            """
            SELECT name
            FROM `tabSynora Agent Run`
            WHERE name = %s
            FOR UPDATE
            """,
            (run_id,),
            as_dict=True,
        )
        if not locked:
            raise GatewayFault("RUN_REJECTED", "run is not available", 404)
    run = frappe.get_doc("Synora Agent Run", run_id)
    actor = str(getattr(frappe.session, "user", "Guest") or "Guest")
    if actor == "Guest" or (
        actor != str(run.initiator) and "System Manager" not in frappe.get_roles(actor)
    ):
        raise GatewayFault("RUN_REJECTED", "run is not available", 404)
    return run


def _run_result(run: Any, chain: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(run.name),
        "run_state": str(run.run_state),
        "status": str(run.status),
        "state_version": int(run.state_version),
        "chain": chain,
    }


def _active_reservations(run_id: str) -> list[dict[str, Any]]:
    return frappe.get_all(
        "Synora Execution Reservation",
        filters={"run": run_id, "status": ["in", ["STARTED", "RECONCILIATION_REQUIRED"]]},
        fields=["reservation_id", "action", "status"],
        limit_page_length=20,
        ignore_permissions=True,
    )


def _expire_if_needed(run: Any, correlation_id: str) -> bool:
    """Close an expired PLAN_EXECUTE Run only when no side effect is active."""

    if str(run.status) != "ACTIVE" or bool(run.revoked) or not workflow_expired(run):
        return False
    active = _active_reservations(str(run.name))
    if active:
        raise GatewayFault(
            "UNCERTAIN_RESULT",
            "P2P Run deadline passed while a side effect still needs reconciliation",
            503,
        )
    if str(run.run_state) not in {
        "CREATED",
        "ANALYZING",
        "PROPOSED",
        "AWAITING_APPROVAL",
        "EXECUTING",
        "RECONCILIATION_REQUIRED",
    }:
        return False
    _mark_terminal(run, "EXPIRED", correlation_id)
    frappe.db.commit()
    return True


def _mark_terminal(run: Any, target: str, correlation_id: str) -> None:
    if str(run.run_state) != target:
        validate_transition(str(run.run_state), target)
        _set_run_state(run, target)
    run.flags.synora_revocation = True
    run.revoked = 1
    run.status = "EXPIRED" if target == "EXPIRED" else "REVOKED"
    run.revoked_at = now_datetime()
    run.revoked_by = frappe.session.user
    run.revocation_correlation_id = correlation_id
    run.save(ignore_permissions=True)


def finalize_p2p_run(run_id: str, correlation_id: str) -> dict[str, Any]:
    """Close a P2P Run only when every persisted step has a verified Receipt."""

    run = _authorized_run(run_id, lock=True)
    if str(run.run_state) == "SUCCEEDED":
        return _run_result(run, get_p2p_chain(run_id, run_state=str(run.run_state)))
    if str(run.status) != "ACTIVE" or bool(run.revoked):
        raise GatewayFault("CONFLICT", "P2P Run is no longer active", 409)
    entries = _entries_for_run(run_id)
    chain = chain_from_entries(str(run.run_state), entries)
    if not chain["completion_ready"]:
        reason = "; ".join(chain["blocked_reasons"][:3]) or str(chain["status"])
        raise GatewayFault("CONFLICT", f"P2P Run is not ready to close: {reason}", 409)
    if str(run.run_state) not in {"EXECUTING", "RECONCILIATION_REQUIRED"}:
        raise GatewayFault("CONFLICT", "P2P Run is not in an executable lifecycle state", 409)
    _mark_terminal(run, "SUCCEEDED", correlation_id)
    frappe.db.commit()
    return _run_result(run, chain_from_entries("SUCCEEDED", entries))


def cancel_p2p_run(run_id: str, correlation_id: str) -> dict[str, Any]:
    """Stop future P2P scheduling while leaving completed ERP business facts intact."""

    run = _authorized_run(run_id, lock=True)
    if str(run.run_state) == "CANCELLED":
        return _run_result(run, get_p2p_chain(run_id, run_state="CANCELLED"))
    if str(run.status) != "ACTIVE" or bool(run.revoked):
        raise GatewayFault("CONFLICT", "P2P Run is no longer active", 409)
    active = _active_reservations(run_id)
    if active:
        raise GatewayFault(
            "UNCERTAIN_RESULT",
            "P2P Run has an active or uncertain side effect; reconcile before cancelling",
            503,
        )
    if str(run.run_state) not in {
        "CREATED",
        "ANALYZING",
        "PROPOSED",
        "AWAITING_APPROVAL",
        "EXECUTING",
        "RECONCILIATION_REQUIRED",
    }:
        raise GatewayFault("CONFLICT", "P2P Run cannot be cancelled in its current state", 409)
    if _expire_if_needed(run, correlation_id):
        return _run_result(run, get_p2p_chain(run_id, run_state="EXPIRED"))
    _mark_terminal(run, "CANCELLED", correlation_id)
    frappe.db.commit()
    return _run_result(run, get_p2p_chain(run_id, run_state="CANCELLED"))


def resume_p2p_run(run_id: str, correlation_id: str) -> dict[str, Any]:
    """Re-read current ERP targets and surface drift without executing a writer."""

    run = _authorized_run(run_id, lock=True)
    if str(run.run_state) in {"CANCELLED", "EXPIRED", "FAILED", "SUCCEEDED"}:
        return _run_result(run, get_p2p_chain(run_id, run_state=str(run.run_state)))
    if _expire_if_needed(run, correlation_id):
        return _run_result(run, get_p2p_chain(run_id, run_state="EXPIRED"))
    # Rebuild the projection before annotating drift so a restarted process
    # can recover even when the previous request stopped before persistence.
    sync_p2p_plan_steps(run_id)
    drift = live_reinvestigation(run_id, str(frappe.session.user))
    chain = get_p2p_chain(run_id, run_state=str(run.run_state))
    if drift["needs_reinvestigation"]:
        for item in drift["drift"]:
            step_name = frappe.db.get_value(
                "Synora P2P Plan Step", {"action": item["action_id"]}, "name"
            )
            if not step_name:
                continue
            step = frappe.get_doc("Synora P2P Plan Step", step_name)
            step.state = "REINVESTIGATION_REQUIRED"
            step.reinvestigation_required = 1
            step.blocked_reason = item["reason"][:2_000]
            step.last_evaluated_at = now_datetime()
            step.flags[P2P_STEP_SERVICE_FLAG] = True
            step.save(ignore_permissions=True)
        if str(run.run_state) == "EXECUTING":
            _set_run_state(run, "RECONCILIATION_REQUIRED")
            run = frappe.get_doc("Synora Agent Run", run_id)
        chain["status"] = "REINVESTIGATION_REQUIRED"
        chain["needs_reinvestigation"] = True
        chain["reinvestigation"] = drift
        frappe.db.commit()
    else:
        chain["reinvestigation"] = drift
    return _run_result(run, chain)


__all__ = [
    "P2P_STEP_SCHEMA_VERSION",
    "assert_p2p_action_dependencies",
    "chain_from_entries",
    "derive_step_views",
    "ensure_p2p_plan_step",
    "get_p2p_chain",
    "infer_dependencies",
    "live_reinvestigation",
    "sync_p2p_plan_steps",
]
