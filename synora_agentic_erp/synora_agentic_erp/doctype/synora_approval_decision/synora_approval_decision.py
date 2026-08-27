import frappe
from frappe.model.document import Document

from synora_agentic_erp.governance.contracts import parse_approval_decision

SERVICE_FLAG = "synora_governance_service"


class SynoraApprovalDecision(Document):  # type: ignore[misc]
    def validate(self) -> None:
        if not self.flags.get(SERVICE_FLAG):
            frappe.throw("ApprovalDecision records require the deterministic governance service")
        try:
            parse_approval_decision(
                {
                    "decision_id": self.decision_id,
                    "action_id": self.action,
                    "proposal_digest": self.proposal_digest,
                    "actor": self.actor,
                    "decision": self.decision,
                    "matched_rule": self.matched_rule,
                    "snapshot_ref": self.snapshot_ref,
                    "expires_at": self.expires_at,
                    "reason": self.reason,
                    "decided_at": self.decided_at,
                    "correlation_id": self.correlation_id,
                }
            )
        except Exception as error:
            frappe.throw(f"ApprovalDecision contract is invalid: {error}")
        if not self.is_new():
            frappe.throw("ApprovalDecision records are immutable")

    def on_trash(self) -> None:
        frappe.throw("ApprovalDecision records cannot be deleted")
