"""Deterministic Phase 6 policy, approval, and execution re-checks.

This module is deliberately the last boundary before the future MR/PO writer.
It can create governance facts, but it never imports or calls an ERP business
document controller.  Frappe is imported lazily so the pure gate-order tests
remain runnable without a bench environment.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from synora_agentic_erp.gateway.contract import GatewayFault, bounded_text, canonical_uuid

RULE_ID = "P6-MAP-20260827-v1"
RULE_VERSION = "1"
GATE_ORDER = ("identity", "scope", "permission", "deterministic", "workflow_policy")
GATE_STATUSES = frozenset({"PASS", "FAIL", "UNKNOWN"})
TARGET_DOCTYPES = frozenset({"Material Request", "Purchase Order"})


def _frappe() -> Any:
    # Importing policy must not make the contract-only test suite depend on a
    # running Frappe process.
    import frappe

    return frappe


@dataclass(frozen=True)
class GateResult:
    """A typed, fail-closed result for one ordered policy gate."""

    status: str
    reason: str

    def __post_init__(self) -> None:
        if self.status not in GATE_STATUSES:
            raise GatewayFault("INVALID_INPUT", "policy gate result is invalid")
        if not isinstance(self.reason, str) or not self.reason or len(self.reason) > 2_000:
            raise GatewayFault("INVALID_INPUT", "policy gate reason is invalid")


GateCheck = Callable[[], GateResult]


def evaluate_gate_sequence(gates: Iterable[tuple[str, GateCheck]]) -> dict[str, GateResult]:
    """Evaluate gates in order and never run a gate after a non-PASS result.

    A later gate is represented as UNKNOWN, rather than being queried, when an
    earlier gate failed or could not be verified.  This prevents a permission
    or object lookup from becoming an information oracle after an identity or
    scope failure.
    """

    results: dict[str, GateResult] = {}
    blocked_by: str | None = None
    for name, check in gates:
        if name not in GATE_ORDER or name in results:
            raise GatewayFault("INVALID_INPUT", "policy gate order is invalid")
        if blocked_by is not None:
            results[name] = GateResult("UNKNOWN", f"blocked by {blocked_by}")
            continue
        try:
            result = check()
        except GatewayFault as error:
            # Do not expose a lower-level query message to a caller.  A
            # rejected permission is deterministic; infrastructure ambiguity
            # remains UNKNOWN and therefore cannot authorize a write.
            status = "FAIL" if error.status_code < 500 else "UNKNOWN"
            result = GateResult(status, "policy gate could not be verified")
        except Exception:
            result = GateResult("UNKNOWN", "policy gate could not be verified")
        if not isinstance(result, GateResult):
            raise GatewayFault("INVALID_INPUT", "policy gate did not return a typed result")
        results[name] = result
        if result.status != "PASS":
            blocked_by = name
    return results


def stricter_outcome(left: str, right: str) -> str:
    """Return the stricter of two policy outcomes (FAIL > UNKNOWN > PASS)."""

    rank = {"PASS": 0, "UNKNOWN": 1, "FAIL": 2}
    if left not in rank or right not in rank:
        raise GatewayFault("INVALID_INPUT", "policy outcome is invalid")
    return left if rank[left] >= rank[right] else right


@dataclass(frozen=True)
class PreExecuteContext:
    """Typed proof passed to the future deterministic ERP writer."""

    action_id: str
    run_id: str
    initiator: str
    executor: str
    target_doctype: str
    payload: dict[str, Any]
    proposal_digest: str
    idempotency_key: str
    snapshot_ref: str
    expires_at: str
    approval_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "run_id": self.run_id,
            "initiator": self.initiator,
            "executor": self.executor,
            "target_doctype": self.target_doctype,
            "payload": self.payload,
            "proposal_digest": self.proposal_digest,
            "idempotency_key": self.idempotency_key,
            "snapshot_ref": self.snapshot_ref,
            "expires_at": self.expires_at,
            "approval_id": self.approval_id,
        }


def _now_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _now() -> Any:
    value = _frappe().utils.now_datetime()
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _frappe_datetime(value: object) -> Any:
    parsed = _frappe().utils.get_datetime(value)
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _actor() -> str:
    frappe = _frappe()
    actor = str(getattr(frappe.session, "user", "Guest") or "Guest")
    if actor == "Guest":
        raise GatewayFault("AUTHENTICATION_REQUIRED", "authenticated user required", 401)
    return actor


def _safe_digest(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise GatewayFault("INVALID_INPUT", "proposal_digest is invalid")
    return value


def _safe_decision(value: object) -> str:
    if value not in {"ALLOW", "DECLINE", "CHANGES_REQUESTED"}:
        raise GatewayFault("INVALID_INPUT", "approval decision is invalid")
    return str(value)


def _safe_reason(value: object) -> str:
    try:
        return bounded_text(value, "reason", 2_000)
    except GatewayFault:
        raise


def _safe_target(action: Any) -> str:
    target = {"CREATE_MR_DRAFT": "Material Request", "CREATE_PO_DRAFT": "Purchase Order"}.get(
        action.action_type
    )
    if target is None:
        raise GatewayFault("INVALID_INPUT", "action_type is invalid")
    return target


def _load_run(run_id: str) -> Any:
    frappe = _frappe()
    try:
        return frappe.get_doc("Synora Agent Run", run_id)
    except frappe.DoesNotExistError as error:
        raise GatewayFault("RUN_REJECTED", "run is not available", 404) from error


def _lock_run(run_id: str) -> Any:
    frappe = _frappe()
    rows = frappe.db.sql(
        """
        SELECT name, initiator, company_scope, warehouse_scope, status, revoked,
               expires_at, workflow_expires_at, execution_mode, run_state,
               state_version
        FROM `tabSynora Agent Run`
        WHERE name = %s
        FOR UPDATE
        """,
        (run_id,),
        as_dict=True,
    )
    if not rows:
        raise GatewayFault("RUN_REJECTED", "run is not available", 404)
    return frappe.get_doc("Synora Agent Run", rows[0].name)


def _lock_action(action_id: str) -> tuple[Any, Any, dict[str, Any]]:
    frappe = _frappe()
    rows = frappe.db.sql(
        """
        SELECT name, state, state_version, initiator, proposal_digest,
               idempotency_key, snapshot_ref, expires_at, run
        FROM `tabSynora Proposed Action`
        WHERE name = %s
        FOR UPDATE
        """,
        (action_id,),
        as_dict=True,
    )
    if not rows:
        raise GatewayFault("NOT_FOUND", "governed action is not available", 404)
    doc = frappe.get_doc("Synora Proposed Action", action_id)
    from synora_agentic_erp.governance.contracts import build_proposed_action

    try:
        action = build_proposed_action(_action_dict(doc))
    except Exception as error:
        raise GatewayFault("CONFLICT", "governed action record is invalid", 409) from error
    return (
        doc,
        action,
        {
            "state": str(rows[0].state),
            "state_version": int(rows[0].state_version),
            "initiator": str(rows[0].initiator),
            "proposal_digest": str(rows[0].proposal_digest),
            "idempotency_key": str(rows[0].idempotency_key),
            "snapshot_ref": str(rows[0].snapshot_ref),
            "expires_at": str(rows[0].expires_at),
            "run": str(rows[0].run),
        },
    )


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


def _run_identity(
    action: Any, run: Any, actor: str, *, approved_actor: str | None = None
) -> GateResult:
    if actor == "Guest" or not getattr(run, "initiator", None):
        return GateResult("FAIL", "authenticated identity is unavailable")
    if action.initiator != run.initiator:
        return GateResult("FAIL", "action and Run initiators conflict")
    if approved_actor is None and actor != run.initiator:
        return GateResult("FAIL", "session user is not the Run initiator")
    if approved_actor is not None and actor != approved_actor:
        return GateResult("FAIL", "session user is not the approved executor")
    frappe = _frappe()
    try:
        if not frappe.db.get_value("User", run.initiator, "enabled"):
            return GateResult("FAIL", "Run initiator is disabled")
        if (
            str(run.status) != "ACTIVE"
            or bool(run.revoked)
            or str(run.run_state)
            not in {"CREATED", "ANALYZING", "PROPOSED", "AWAITING_APPROVAL", "EXECUTING"}
        ):
            return GateResult("FAIL", "Run is no longer active")
        if _frappe_datetime(run.expires_at) <= _now():
            return GateResult("FAIL", "Run has expired")
    except Exception:
        return GateResult("UNKNOWN", "Run identity could not be verified")
    return GateResult("PASS", "server session and Run identity match")


def _scope_identity(action: Any, run: Any, actor: str) -> GateResult:
    payload = action.payload
    if payload.get("company") != run.company_scope:
        return GateResult("FAIL", "company scope does not match the Run")
    warehouses = [str(item.get("warehouse", "")) for item in payload.get("items", [])]
    if not warehouses or any(not value for value in warehouses):
        return GateResult("FAIL", "warehouse scope is incomplete")
    if run.warehouse_scope and any(value != run.warehouse_scope for value in warehouses):
        return GateResult("FAIL", "warehouse scope does not match the Run")
    frappe = _frappe()
    try:
        company = frappe.get_list(
            "Company",
            pluck="name",
            filters={"name": run.company_scope},
            user=actor,
            limit=1,
        )
        if not company:
            return GateResult("FAIL", "company is outside the current user scope")
        for warehouse in sorted(set(warehouses)):
            visible = frappe.get_list(
                "Warehouse",
                pluck="name",
                filters={
                    "name": warehouse,
                    "company": run.company_scope,
                    "disabled": 0,
                },
                user=actor,
                limit=1,
            )
            if not visible:
                return GateResult("FAIL", "warehouse is outside the current user scope")
    except Exception:
        return GateResult("UNKNOWN", "company or warehouse scope could not be verified")
    return GateResult("PASS", "company and warehouse scope match current permissions")


def _permission(action: Any, actor: str) -> GateResult:
    frappe = _frappe()
    target = _safe_target(action)
    try:
        required: tuple[tuple[str, str], ...] = (
            (target, "read"),
            (target, "create"),
            ("Company", "read"),
            ("Warehouse", "read"),
            ("Item", "read"),
        )
        if target == "Purchase Order":
            required += (
                ("Supplier", "read"),
                ("Currency", "read"),
                ("Price List", "read"),
            )
        if any(
            not frappe.has_permission(doctype, ptype, user=actor) for doctype, ptype in required
        ):
            return GateResult("FAIL", "current ERP permission is insufficient")
    except Exception:
        return GateResult("UNKNOWN", "current ERP permission could not be verified")
    return GateResult("PASS", "current ERP permissions are sufficient")


def _visible_item(code: str, actor: str) -> Any | None:
    frappe = _frappe()
    rows = frappe.get_list(
        "Item",
        filters={"name": code},
        fields=["name", "disabled", "stock_uom"],
        user=actor,
        limit=1,
    )
    return rows[0] if rows else None


def _visible_supplier(name: str, actor: str) -> Any | None:
    frappe = _frappe()
    rows = frappe.get_list(
        "Supplier",
        filters={"name": name},
        fields=["name", "disabled"],
        user=actor,
        limit=1,
    )
    return rows[0] if rows else None


def _purchase_price_rate(
    item: dict[str, Any],
    item_row: Any,
    *,
    supplier: str,
    buying_price_list: str,
    transaction_date: str,
) -> Decimal | None:
    """Resolve the current buying rate through ERPNext's read-only price source.

    The controller's ``set_missing_values`` can create an Item Price when a
    caller supplies a rate.  Policy evaluation must not use that side effect as
    its source of truth, so this calls the upstream resolver directly before any
    ERP document writer is reachable.  A missing source is a hard rejection.
    """

    from erpnext.stock.get_item_details import get_price_list_rate_for

    source_rate = get_price_list_rate_for(
        {
            "item_code": str(item["item_code"]),
            "price_list": buying_price_list,
            "buying_price_list": buying_price_list,
            "supplier": supplier,
            "uom": str(item.get("uom") or getattr(item_row, "stock_uom", "")),
            "stock_uom": str(getattr(item_row, "stock_uom", "")),
            "qty": item["qty"],
            "transaction_date": transaction_date,
            "transaction_type": "buying",
            "conversion_factor": 1,
        },
        str(item["item_code"]),
    )
    if source_rate is None:
        return None
    return Decimal(str(source_rate))


def _open_duplicate(
    parent: str,
    child: str,
    item_code: str,
    warehouse: str,
    company: str,
    actor: str,
) -> bool:
    frappe = _frappe()
    rows = frappe.get_list(
        child,
        filters={"item_code": item_code, "warehouse": warehouse},
        fields=["parent"],
        user=actor,
        parent_doctype=parent,
        limit=100,
    )
    parents = sorted({str(row.parent) for row in rows if getattr(row, "parent", None)})
    if not parents:
        return False
    current = frappe.get_list(
        parent,
        filters={"name": ["in", parents], "company": company, "docstatus": ["<", 2]},
        fields=["name"],
        user=actor,
        limit=100,
    )
    return bool(current)


def _deterministic(action: Any, actor: str) -> GateResult:
    frappe = _frappe()
    payload = action.payload
    target = _safe_target(action)
    try:
        transaction_date = datetime.strptime(payload["transaction_date"], "%Y-%m-%d").date()
        price_list_currency: str | None = None
        if target == "Purchase Order":
            currency_rows = frappe.get_list(
                "Currency",
                pluck="name",
                filters={"name": payload["currency"]},
                user=actor,
                limit=1,
            )
            if not currency_rows:
                return GateResult("FAIL", "currency is unavailable")
            price_list_rows = frappe.get_list(
                "Price List",
                fields=["name", "currency"],
                filters={
                    "name": payload["buying_price_list"],
                    "buying": 1,
                    "enabled": 1,
                },
                user=actor,
                limit=1,
            )
            if not price_list_rows:
                return GateResult("FAIL", "buying price list is unavailable")
            price_list_currency = str(getattr(price_list_rows[0], "currency", "") or "")
            if price_list_currency != payload["currency"]:
                return GateResult("FAIL", "price list currency does not match payload currency")
        seen: set[tuple[str, str]] = set()
        for item in payload["items"]:
            item_code = str(item["item_code"])
            warehouse = str(item["warehouse"])
            key = (item_code, warehouse)
            if key in seen:
                return GateResult("FAIL", "duplicate item lines are not allowed")
            seen.add(key)
            qty = Decimal(str(item["qty"]))
            if not qty.is_finite() or qty <= 0:
                return GateResult("FAIL", "quantity must be positive and finite")
            schedule = datetime.strptime(item["schedule_date"], "%Y-%m-%d").date()
            if schedule < transaction_date:
                return GateResult("FAIL", "schedule date precedes transaction date")
            item_row = _visible_item(item_code, actor)
            if item_row is None or bool(getattr(item_row, "disabled", 0)):
                return GateResult("FAIL", "item is unavailable")
            if item.get("uom") and not frappe.db.exists("UOM", item["uom"]):
                return GateResult("FAIL", "item UOM is unavailable")
            if target == "Purchase Order":
                rate = Decimal(str(item["rate"]))
                if not rate.is_finite() or rate <= 0:
                    return GateResult("FAIL", "rate must be finite and positive")
                source_rate = _purchase_price_rate(
                    item,
                    item_row,
                    supplier=str(payload["supplier"]),
                    buying_price_list=str(payload["buying_price_list"]),
                    transaction_date=str(payload["transaction_date"]),
                )
                if source_rate is None:
                    return GateResult("FAIL", "authoritative buying price is unavailable")
                if source_rate != rate:
                    return GateResult("FAIL", "rate differs from authoritative buying price")
            if _open_duplicate(
                target,
                "Material Request Item" if target == "Material Request" else "Purchase Order Item",
                item_code,
                warehouse,
                payload["company"],
                actor,
            ):
                return GateResult("FAIL", "an open document already covers this item")
            if target == "Purchase Order":
                material_request = item.get("material_request")
                if material_request:
                    prerequisite = frappe.get_list(
                        "Material Request",
                        filters={
                            "name": material_request,
                            "company": payload["company"],
                            "docstatus": ["<", 2],
                        },
                        fields=["name"],
                        user=actor,
                        limit=1,
                    )
                    if not prerequisite:
                        return GateResult("FAIL", "material request prerequisite is unavailable")
        if target == "Purchase Order":
            supplier = _visible_supplier(str(payload["supplier"]), actor)
            if supplier is None or bool(getattr(supplier, "disabled", 0)):
                return GateResult("FAIL", "supplier is unavailable")
            schedule = datetime.strptime(payload["schedule_date"], "%Y-%m-%d").date()
            if schedule < transaction_date:
                return GateResult("FAIL", "schedule date precedes transaction date")
    except InvalidOperation, TypeError, ValueError, KeyError:
        return GateResult("FAIL", "deterministic payload checks failed")
    except Exception:
        return GateResult("UNKNOWN", "current ERP objects could not be verified")
    return GateResult("PASS", "quantity, object, prerequisite, and duplicate checks passed")


def _workflow_policy(action: Any, actor: str) -> GateResult:
    del actor
    frappe = _frappe()
    target = _safe_target(action)
    try:
        active = frappe.get_all(
            "Workflow",
            filters={"document_type": target, "is_active": 1},
            fields=["name", "document_type"],
            limit=20,
        )
    except Exception:
        return GateResult("UNKNOWN", "active ERP Workflow could not be verified")
    if active:
        # Step 001 mapped the fixed site only when no active Workflow existed.
        # A new or conflicting Workflow requires a fresh mapping; never copy or
        # evaluate arbitrary Workflow expressions in the Agent runtime.
        return GateResult("UNKNOWN", "active ERP Workflow requires a new mapping")
    return GateResult("PASS", "fixed development mapping permits explicit confirmation")


def _expiry_passes(expires_at: str) -> bool:
    try:
        return bool(_frappe_datetime(expires_at) > _now())
    except Exception:
        return False


def _latest_policy(action_id: str, digest: str) -> Any | None:
    frappe = _frappe()
    rows = frappe.get_all(
        "Synora Policy Decision",
        filters={"action": action_id, "proposal_digest": digest, "outcome": "ALLOW"},
        fields=["name", "decision_id", "actor", "snapshot_ref", "expires_at", "correlation_id"],
        order_by="decided_at desc, creation desc",
        limit=1,
    )
    return rows[0] if rows else None


def _latest_approval(action_id: str, digest: str, decision: str = "ALLOW") -> Any | None:
    frappe = _frappe()
    rows = frappe.get_all(
        "Synora Approval Decision",
        filters={"action": action_id, "proposal_digest": digest, "decision": decision},
        fields=[
            "name",
            "decision_id",
            "actor",
            "decision",
            "snapshot_ref",
            "expires_at",
            "reason",
            "decided_at",
            "correlation_id",
        ],
        order_by="decided_at desc, creation desc",
        limit=1,
    )
    return rows[0] if rows else None


def _gate_checks(
    action: Any,
    run: Any,
    actor: str,
    *,
    approved_actor: str | None = None,
) -> dict[str, GateResult]:
    return evaluate_gate_sequence(
        [
            ("identity", lambda: _run_identity(action, run, actor, approved_actor=approved_actor)),
            ("scope", lambda: _scope_identity(action, run, actor)),
            ("permission", lambda: _permission(action, actor)),
            ("deterministic", lambda: _deterministic(action, actor)),
            ("workflow_policy", lambda: _workflow_policy(action, actor)),
        ]
    )


def _policy_reason(checks: dict[str, GateResult], *, expired: bool = False) -> str:
    if expired:
        return "proposal expiry is no longer valid"
    for name in GATE_ORDER:
        result = checks[name]
        if result.status != "PASS":
            return f"{name} gate did not pass: {result.reason}"
    return "all policy gates passed"


def _action_response(action: Any, doc: Any) -> dict[str, Any]:
    result: dict[str, Any] = dict(action.to_dict())
    result.update(
        {
            "state": str(doc.state),
            "state_version": int(doc.state_version),
            "state_reason": str(doc.state_reason or ""),
            "state_changed_at": str(doc.state_changed_at or ""),
            "state_changed_by": str(doc.state_changed_by or ""),
        }
    )
    return result


def evaluate_proposal(value: object) -> dict[str, Any]:
    """Parse, evaluate, persist, and state-transition one governed proposal."""

    from synora_agentic_erp.governance.contracts import (
        PolicyDecision,
        build_proposed_action,
    )
    from synora_agentic_erp.governance.service import (
        new_decision_id,
        persist_policy_decision,
        persist_proposed_action,
        transition_action_state,
    )

    action = build_proposed_action(value)
    actor = _actor()
    run = _load_run(action.run_id)
    identity = _run_identity(action, run, actor)
    if identity.status != "PASS":
        raise GatewayFault("PERMISSION_DENIED", identity.reason, 403)
    # The action is inserted only after the server identity gate passes.  From
    # this point all five gates and their rejection fact are one transaction.
    persist_proposed_action(action)
    checks = _gate_checks(action, run, actor)
    expired = not _expiry_passes(action.expires_at)
    statuses = {name: checks[name].status for name in GATE_ORDER}
    synora_outcome = "PASS" if all(value == "PASS" for value in statuses.values()) else "FAIL"
    effective_outcome = stricter_outcome(synora_outcome, statuses["workflow_policy"])
    outcome = "ALLOW" if not expired and effective_outcome == "PASS" else "REJECT"
    decision = PolicyDecision(
        decision_id=new_decision_id(),
        action_id=action.action_id,
        proposal_digest=action.proposal_digest,
        actor=actor,
        checks=statuses,
        matched_rule=RULE_ID,
        rule_version=RULE_VERSION,
        outcome=outcome,
        reason=_policy_reason(checks, expired=expired),
        snapshot_ref=action.snapshot_ref,
        expires_at=action.expires_at,
        decided_at=_now_timestamp(),
        correlation_id=action.correlation_id,
    )
    stored_policy = persist_policy_decision(decision)
    target_state = "AWAITING_APPROVAL" if outcome == "ALLOW" else "POLICY_REJECTED"
    stored_action = transition_action_state(
        action.action_id,
        target_state,
        expected_version=1,
        reason=decision.reason,
        correlation_id=action.correlation_id,
    )
    return {"action": stored_action, "policy": stored_policy}


def _approver_allowed(action: Any, actor: str) -> None:
    frappe = _frappe()
    if action.approval_class == "INITIATOR_CONFIRMATION":
        if actor != action.initiator:
            raise GatewayFault("PERMISSION_DENIED", "initiator confirmation is required", 403)
        return
    if actor == action.initiator:
        raise GatewayFault(
            "PERMISSION_DENIED",
            "independent approval requires a different user",
            403,
        )
    target = _safe_target(action)
    try:
        if not frappe.has_permission(target, "read", user=actor) or not frappe.has_permission(
            target, "create", user=actor
        ):
            raise GatewayFault("PERMISSION_DENIED", "approver permission is insufficient", 403)
    except GatewayFault:
        raise
    except Exception as error:
        raise GatewayFault("UNKNOWN", "approver permission could not be verified", 409) from error


def decide_action(
    action_id: object,
    decision: object,
    proposal_digest: object,
    reason: object,
    correlation_id: object,
) -> dict[str, Any]:
    """Record a session-bound approval and CAS the action state if needed."""

    from synora_agentic_erp.governance.contracts import ApprovalDecision
    from synora_agentic_erp.governance.service import (
        new_decision_id,
        persist_approval_decision,
        transition_action_state,
    )

    safe_action_id = canonical_uuid(action_id, "action_id")
    safe_digest = _safe_digest(proposal_digest)
    safe_decision = _safe_decision(decision)
    safe_reason = _safe_reason(reason)
    safe_correlation = canonical_uuid(correlation_id, "correlation_id")
    actor = _actor()
    run = _lock_run_for_action(safe_action_id)
    doc, action, locked = _lock_action(safe_action_id)
    if action.run_id != run.name or action.proposal_digest != safe_digest:
        raise GatewayFault("CONFLICT", "approval digest or Run conflicts", 409)
    if locked["state"] != "AWAITING_APPROVAL":
        raise GatewayFault("CONFLICT", "governed action is not awaiting approval", 409)
    if not _expiry_passes(action.expires_at) or str(run.status) != "ACTIVE" or bool(run.revoked):
        raise GatewayFault("CONFLICT", "governed action has expired", 409)
    _approver_allowed(action, actor)
    # Policy belongs to the proposal's Run initiator.  An independent
    # approver is checked separately below; using the approver as the policy
    # actor here would incorrectly fail every separation-of-duties action at
    # the identity gate.
    checks = _gate_checks(action, run, action.initiator)
    if any(result.status != "PASS" for result in checks.values()):
        raise GatewayFault("CONFLICT", "current policy no longer permits approval", 409)
    policy = _latest_policy(action.action_id, action.proposal_digest)
    if (
        policy is None
        or policy.snapshot_ref != action.snapshot_ref
        or not _expiry_passes(str(policy.expires_at))
    ):
        raise GatewayFault("CONFLICT", "approved policy is stale", 409)
    approval = ApprovalDecision(
        decision_id=new_decision_id(),
        action_id=action.action_id,
        proposal_digest=action.proposal_digest,
        actor=actor,
        decision=safe_decision,
        matched_rule=RULE_ID,
        snapshot_ref=action.snapshot_ref,
        expires_at=action.expires_at,
        reason=safe_reason,
        decided_at=_now_timestamp(),
        correlation_id=safe_correlation,
    )
    stored_approval = persist_approval_decision(approval)
    if safe_decision == "CHANGES_REQUESTED":
        return {
            "action": _action_response(action, doc),
            "approval": stored_approval,
        }
    target_state = "APPROVED" if safe_decision == "ALLOW" else "DECLINED"
    stored_action = transition_action_state(
        action.action_id,
        target_state,
        expected_version=locked["state_version"],
        reason=safe_reason,
        correlation_id=safe_correlation,
        approval_digest=action.proposal_digest,
    )
    return {"action": stored_action, "approval": stored_approval}


def _lock_run_for_action(action_id: str) -> Any:
    frappe = _frappe()
    run_id = frappe.db.get_value("Synora Proposed Action", action_id, "run")
    if not run_id:
        raise GatewayFault("NOT_FOUND", "governed action is not available", 404)
    return _lock_run(str(run_id))


def _expire_if_owner(action: Any, doc: Any, actor: str, reason: str) -> None:
    if actor != action.initiator:
        return
    from synora_agentic_erp.governance.service import transition_action_state

    if str(doc.state) == "APPROVED":
        transition_action_state(
            action.action_id,
            "EXPIRED",
            expected_version=int(doc.state_version),
            reason=reason,
            correlation_id=action.correlation_id,
        )


def pre_execute_recheck(
    action_id: object,
    expected_digest: object,
    idempotency_key: object,
) -> PreExecuteContext:
    """Re-check all current facts and return a typed proof without ERP writes."""

    from synora_agentic_erp.governance.contracts import _idempotency_key

    safe_action_id = canonical_uuid(action_id, "action_id")
    safe_digest = _safe_digest(expected_digest)
    safe_key = _idempotency_key(idempotency_key)
    actor = _actor()
    run = _lock_run_for_action(safe_action_id)
    doc, action, locked = _lock_action(safe_action_id)
    if action.run_id != run.name or action.proposal_digest != safe_digest:
        raise GatewayFault("CONFLICT", "execution digest or Run conflicts", 409)
    if action.idempotency_key != safe_key:
        raise GatewayFault("CONFLICT", "idempotency key conflicts", 409)
    if locked["state"] != "APPROVED":
        raise GatewayFault("CONFLICT", "governed action is not approved", 409)
    if _frappe().db.exists("Synora Execution Receipt", {"idempotency_key": safe_key}):
        raise GatewayFault("CONFLICT", "idempotency key is already reserved", 409)
    policy = _latest_policy(action.action_id, action.proposal_digest)
    if (
        policy is None
        or policy.snapshot_ref != action.snapshot_ref
        or not _expiry_passes(str(policy.expires_at))
    ):
        raise GatewayFault("CONFLICT", "policy is missing or stale", 409)
    approval = _latest_approval(action.action_id, action.proposal_digest, "ALLOW")
    if (
        approval is None
        or approval.snapshot_ref != action.snapshot_ref
        or not _expiry_passes(str(approval.expires_at))
    ):
        raise GatewayFault("CONFLICT", "approval is missing or stale", 409)
    approved_actor = (
        None if action.approval_class == "INITIATOR_CONFIRMATION" else str(approval.actor)
    )
    if action.approval_class == "INDEPENDENT_APPROVER" and actor != approved_actor:
        raise GatewayFault("PERMISSION_DENIED", "executor is not the approving user", 403)
    checks = _gate_checks(action, run, actor, approved_actor=approved_actor)
    if not _expiry_passes(action.expires_at):
        _expire_if_owner(action, doc, actor, "execution proof expired")
        raise GatewayFault("CONFLICT", "governed action has expired", 409)
    failed = next((name for name in GATE_ORDER if checks[name].status != "PASS"), None)
    if failed is not None:
        _expire_if_owner(action, doc, actor, f"execution proof stale at {failed} gate")
        raise GatewayFault("CONFLICT", f"execution proof failed at {failed} gate", 409)
    target = _safe_target(action)
    return PreExecuteContext(
        action_id=action.action_id,
        run_id=action.run_id,
        initiator=action.initiator,
        executor=actor,
        target_doctype=target,
        payload=action.payload,
        proposal_digest=action.proposal_digest,
        idempotency_key=action.idempotency_key,
        snapshot_ref=action.snapshot_ref,
        expires_at=action.expires_at,
        approval_id=str(approval.decision_id),
    )


def get_action(action_id: object) -> dict[str, Any]:
    """Return a proposal only to its owner or an effective approver."""

    safe_action_id = canonical_uuid(action_id, "action_id")
    actor = _actor()
    doc, action, locked = _lock_action_for_read(safe_action_id)
    if actor != action.initiator and "System Manager" not in _frappe().get_roles(actor):
        if locked["state"] != "AWAITING_APPROVAL":
            raise GatewayFault("PERMISSION_DENIED", "governed action is not available", 403)
        _approver_allowed(action, actor)
        run = _load_run(action.run_id)
        scope = _scope_identity(action, run, actor)
        if scope.status != "PASS":
            raise GatewayFault("PERMISSION_DENIED", "governed action is not available", 403)
        permission = _permission(action, actor)
        if permission.status != "PASS":
            raise GatewayFault("PERMISSION_DENIED", "governed action is not available", 403)
    return _action_response(action, doc)


def _lock_action_for_read(action_id: str) -> tuple[Any, Any, dict[str, Any]]:
    # Reads do not need a lock; this helper shares the strict parser with the
    # mutating paths but avoids a write-looking API call.
    frappe = _frappe()
    try:
        doc = frappe.get_doc("Synora Proposed Action", action_id)
    except frappe.DoesNotExistError as error:
        raise GatewayFault("NOT_FOUND", "governed action is not available", 404) from error
    from synora_agentic_erp.governance.contracts import build_proposed_action

    try:
        action = build_proposed_action(_action_dict(doc))
    except Exception as error:
        raise GatewayFault("CONFLICT", "governed action record is invalid", 409) from error
    return doc, action, {"state": str(doc.state)}


__all__ = [
    "GATE_ORDER",
    "GateResult",
    "PreExecuteContext",
    "decide_action",
    "evaluate_gate_sequence",
    "evaluate_proposal",
    "get_action",
    "pre_execute_recheck",
    "stricter_outcome",
]
