import json

import frappe
from frappe.model.document import Document

from synora_agentic_erp.governance.contracts import create_execution_receipt

SERVICE_FLAG = "synora_governance_service"
VERIFIED_FLAG = "synora_verified_execution"


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
            frappe.throw("ExecutionReceipt records are immutable")

    def on_trash(self) -> None:
        frappe.throw("ExecutionReceipt records cannot be deleted")
