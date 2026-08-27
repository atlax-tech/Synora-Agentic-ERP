import frappe
from frappe.model.document import Document

from synora_agentic_erp.governance.contracts import parse_policy_decision

SERVICE_FLAG = "synora_governance_service"


class SynoraPolicyDecision(Document):  # type: ignore[misc]
    def validate(self) -> None:
        if not self.flags.get(SERVICE_FLAG):
            frappe.throw("PolicyDecision records require the deterministic governance service")
        try:
            parse_policy_decision(
                {
                    "decision_id": self.decision_id,
                    "action_id": self.action,
                    "proposal_digest": self.proposal_digest,
                    "actor": self.actor,
                    "checks": {
                        "identity": self.identity_outcome,
                        "scope": self.scope_outcome,
                        "permission": self.permission_outcome,
                        "deterministic": self.deterministic_outcome,
                        "workflow_policy": self.workflow_policy_outcome,
                    },
                    "matched_rule": self.matched_rule,
                    "rule_version": self.rule_version,
                    "outcome": self.outcome,
                    "reason": self.reason,
                    "snapshot_ref": self.snapshot_ref,
                    "expires_at": self.expires_at,
                    "decided_at": self.decided_at,
                    "correlation_id": self.correlation_id,
                }
            )
        except Exception as error:
            frappe.throw(f"PolicyDecision contract is invalid: {error}")
        if not self.is_new():
            frappe.throw("PolicyDecision records are immutable")

    def on_trash(self) -> None:
        frappe.throw("PolicyDecision records cannot be deleted")
