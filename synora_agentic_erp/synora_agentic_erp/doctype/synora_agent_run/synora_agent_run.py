import frappe
from frappe.model.document import Document

IMMUTABLE_FIELDS = {
    "initiator",
    "company_scope",
    "warehouse_scope",
    "goal",
    "time_window_days",
    "capability_digest",
    "capability_audience",
    "issued_at",
    "expires_at",
    "correlation_id",
}
LIFECYCLE_FIELDS = {
    "revoked",
    "status",
    "run_state",
    "state_version",
    "revoked_at",
    "revoked_by",
    "revocation_correlation_id",
}


class SynoraAgentRun(Document):  # type: ignore[misc]
    def validate(self) -> None:
        if self.is_new():
            return
        if any(self.has_value_changed(field) for field in IMMUTABLE_FIELDS):
            frappe.throw("Synora Agent Run identity and scope are immutable")
        lifecycle_changed = any(self.has_value_changed(field) for field in LIFECYCLE_FIELDS)
        controlled = self.flags.synora_revocation or self.flags.synora_state_change
        if lifecycle_changed and not controlled:
            frappe.throw(
                "Synora Agent Run lifecycle changes require the controlled transition path"
            )
