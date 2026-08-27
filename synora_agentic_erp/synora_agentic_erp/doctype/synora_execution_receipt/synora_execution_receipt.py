import json

import frappe
from frappe.model.document import Document

from synora_agentic_erp.governance.contracts import create_execution_receipt

SERVICE_FLAG = "synora_governance_service"
VERIFIED_FLAG = "synora_verified_execution"
RECONCILIATION_TRANSITION_FLAG = "synora_execution_receipt_reconciliation"
IDENTITY_FIELDS = {
    "receipt_id",
    "action",
    "run",
    "idempotency_key",
    "initiator",
    "approver",
    "executor",
    "proposal_digest",
    "started_at",
    "correlation_id",
}
RECONCILIATION_FIELDS = {
    "target_doctype",
    "target_name",
    "verified_fields_json",
    "response_category",
    "failure_category",
    "final_state",
    "completed_at",
    "reconciliation_evidence_json",
}
SYSTEM_FIELDS = {
    "name",
    "owner",
    "creation",
    "modified",
    "modified_by",
    "docstatus",
    "idx",
}


class SynoraExecutionReceipt(Document):  # type: ignore[misc]
    def validate(self) -> None:
        if not self.flags.get(SERVICE_FLAG):
            frappe.throw("ExecutionReceipt records require the deterministic governance service")
        try:
            receipt = create_execution_receipt(
                {
                    "receipt_id": self.receipt_id,
                    "action_id": self.action,
                    "run_id": self.run,
                    "idempotency_key": self.idempotency_key,
                    "initiator": self.initiator,
                    "approver": self.approver or None,
                    "executor": self.executor,
                    "proposal_digest": self.proposal_digest,
                    "target_doctype": self.target_doctype or None,
                    "target_name": self.target_name or None,
                    "verified_fields": json.loads(self.verified_fields_json),
                    "response_category": self.response_category,
                    "failure_category": self.failure_category or None,
                    "final_state": self.final_state,
                    "started_at": self.started_at,
                    "completed_at": self.completed_at or None,
                    "correlation_id": self.correlation_id,
                    "reconciliation_evidence": json.loads(self.reconciliation_evidence_json),
                }
            )
        except Exception as error:
            frappe.throw(f"ExecutionReceipt contract is invalid: {error}")
        if receipt.final_state in {"SUCCEEDED", "RECONCILED_SUCCESS"} and not self.flags.get(
            VERIFIED_FLAG
        ):
            frappe.throw("success Receipt requires verified execution evidence")
        if not self.is_new():
            if not self.flags.get(RECONCILIATION_TRANSITION_FLAG):
                frappe.throw("ExecutionReceipt records are immutable")
            previous = str(self.get_db_value("final_state") or "")
            if previous != "RECONCILIATION_REQUIRED" or self.final_state not in {
                "RECONCILED_SUCCESS",
                "RECONCILED_FAILURE",
                "MANUAL_INTERVENTION",
            }:
                frappe.throw("ExecutionReceipt reconciliation transition is invalid")
            changed_identity = [field for field in IDENTITY_FIELDS if self.has_value_changed(field)]
            if changed_identity:
                frappe.throw("ExecutionReceipt identity is immutable")
            changed_other = [
                field
                for field in self.meta.get_valid_columns()
                if field not in IDENTITY_FIELDS
                and field not in RECONCILIATION_FIELDS
                and field not in SYSTEM_FIELDS
                and self.has_value_changed(field)
            ]
            if changed_other:
                frappe.throw("ExecutionReceipt contains an unsupported reconciliation change")

    def on_trash(self) -> None:
        frappe.throw("ExecutionReceipt records cannot be deleted")
