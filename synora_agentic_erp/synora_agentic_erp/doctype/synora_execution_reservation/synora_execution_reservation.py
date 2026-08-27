import frappe
from frappe.model.document import Document

SERVICE_FLAG = "synora_governance_service"
TRANSITION_FLAG = "synora_execution_reservation_transition"
IDENTITY_FIELDS = {
    "reservation_id",
    "action",
    "run",
    "idempotency_key",
    "proposal_digest",
    "target_doctype",
    "executor",
    "owner_token",
    "lease_expires_at",
    "attempt",
    "started_at",
    "correlation_id",
}
STATUSES = {
    "STARTED",
    "SUCCEEDED",
    "FAILED",
    "RECONCILIATION_REQUIRED",
    "RECONCILED_SUCCESS",
    "RECONCILED_FAILURE",
    "MANUAL_INTERVENTION",
}
TARGET_DOCTYPES = {"Material Request", "Purchase Order"}
TRANSITIONS = {
    "STARTED": {"SUCCEEDED", "FAILED", "RECONCILIATION_REQUIRED"},
    "RECONCILIATION_REQUIRED": {
        "RECONCILED_SUCCESS",
        "RECONCILED_FAILURE",
        "MANUAL_INTERVENTION",
    },
    "SUCCEEDED": set(),
    "FAILED": set(),
    "RECONCILED_SUCCESS": set(),
    "RECONCILED_FAILURE": set(),
    "MANUAL_INTERVENTION": set(),
}
MUTABLE_FIELDS = {
    "status",
    "target_name",
    "receipt",
    "response_category",
    "failure_category",
    "completed_at",
    "reconciliation_count",
    "last_reconciled_at",
}


class SynoraExecutionReservation(Document):  # type: ignore[misc]
    def validate(self) -> None:
        if not self.flags.get(SERVICE_FLAG):
            frappe.throw("Execution reservations require the deterministic governance service")
        if self.target_doctype not in TARGET_DOCTYPES:
            frappe.throw("Execution reservation target is invalid")
        if self.status not in STATUSES:
            frappe.throw("Execution reservation status is invalid")
        if self.is_new():
            if self.status != "STARTED":
                frappe.throw("Execution reservation must start in STARTED status")
            if int(self.attempt or 0) != 1:
                frappe.throw("Execution reservation attempt is invalid")
            return
        changed_identity = [field for field in IDENTITY_FIELDS if self.has_value_changed(field)]
        if changed_identity:
            frappe.throw("Execution reservation identity is immutable")
        changed_mutable = [field for field in MUTABLE_FIELDS if self.has_value_changed(field)]
        if changed_mutable and not self.flags.get(TRANSITION_FLAG):
            frappe.throw("Execution reservation changes require the reconciliation service")
        if self.has_value_changed("status"):
            previous = str(self.get_db_value("status") or "")
            if not self.flags.get(TRANSITION_FLAG) or self.status not in TRANSITIONS.get(
                previous, set()
            ):
                frappe.throw("Execution reservation transition is invalid")

    def on_trash(self) -> None:
        frappe.throw("Execution reservations cannot be deleted")
