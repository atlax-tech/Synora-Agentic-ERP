import json

import frappe
from frappe.model.document import Document

from synora_agentic_erp.governance.contracts import build_proposed_action

SERVICE_FLAG = "synora_governance_service"
STATE_FLAG = "synora_action_state_change"
IMMUTABLE_FIELDS = {
    "schema_version",
    "action_type",
    "run",
    "action_id",
    "initiator",
    "payload_json",
    "evidence_refs_json",
    "calculation_refs_json",
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
STATE_FIELDS = {
    "state",
    "state_version",
    "state_reason",
    "state_changed_at",
    "state_changed_by",
}


def _action_dict(doc: Document) -> dict[str, object]:
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


class SynoraProposedAction(Document):  # type: ignore[misc]
    def validate(self) -> None:
        if not self.flags.get(SERVICE_FLAG):
            frappe.throw("ProposedAction records require the deterministic governance service")
        try:
            action = build_proposed_action(_action_dict(self))
        except Exception as error:
            frappe.throw(f"ProposedAction contract is invalid: {error}")
        if action.proposal_digest != self.proposal_digest:
            frappe.throw("ProposedAction digest does not match typed content")
        if self.is_new():
            if self.state != "DRAFT" or int(self.state_version or 0) != 1:
                frappe.throw("ProposedAction must be created in DRAFT state")
            return
        changed_immutable = [field for field in IMMUTABLE_FIELDS if self.has_value_changed(field)]
        if changed_immutable:
            frappe.throw("ProposedAction reviewed content is immutable")
        changed_state = [field for field in STATE_FIELDS if self.has_value_changed(field)]
        if changed_state and not self.flags.get(STATE_FLAG):
            frappe.throw(
                "ProposedAction state changes require the deterministic transition service"
            )
        if not changed_state and self.flags.get(STATE_FLAG):
            frappe.throw("ProposedAction transition did not change state")

    def on_trash(self) -> None:
        frappe.throw("ProposedAction records cannot be deleted")
