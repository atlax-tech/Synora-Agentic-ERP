"""Persistence guard for one P2P action in a durable Run plan."""

from __future__ import annotations

import json

import frappe
from frappe.model.document import Document

SERVICE_FLAG = "synora_p2p_orchestration_service"
IDENTITY_FIELDS = {"run", "action", "step_id", "step_order", "correlation_id"}
MUTABLE_FIELDS = {
    "depends_on_json",
    "state",
    "target_doctype",
    "target_name",
    "source_doctype",
    "source_name",
    "blocked_reason",
    "reinvestigation_required",
    "observed_digest",
    "last_evaluated_at",
}
STATES = {
    "PLANNED",
    "WAITING_APPROVAL",
    "WAITING_DEPENDENCY",
    "READY",
    "EXECUTING",
    "SUCCEEDED",
    "FAILED",
    "BLOCKED",
    "RECONCILIATION_REQUIRED",
    "REINVESTIGATION_REQUIRED",
    "CANCELLED",
    "EXPIRED",
}


class SynoraP2PPlanStep(Document):  # type: ignore[misc]
    def validate(self) -> None:
        if not self.flags.get(SERVICE_FLAG):
            frappe.throw("P2P plan steps require the deterministic orchestration service")
        if self.state not in STATES:
            frappe.throw("P2P plan step state is invalid")
        try:
            dependencies = json.loads(self.depends_on_json or "[]")
        except (TypeError, ValueError) as error:
            frappe.throw(f"P2P plan step dependencies are invalid: {error}")
        if (
            not isinstance(dependencies, list)
            or len(dependencies) > 64
            or any(not isinstance(value, str) or not value for value in dependencies)
            or len(set(dependencies)) != len(dependencies)
        ):
            frappe.throw("P2P plan step dependencies are invalid")
        if not self.is_new():
            changed_identity = [field for field in IDENTITY_FIELDS if self.has_value_changed(field)]
            if changed_identity:
                frappe.throw("P2P plan step identity is immutable")
            changed_fields = {
                field for field in self.meta.get_valid_columns() if self.has_value_changed(field)
            }
            unsupported = (
                changed_fields
                - MUTABLE_FIELDS
                - {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx"}
            )
            if unsupported:
                frappe.throw("P2P plan step contains an unsupported change")

    def on_trash(self) -> None:
        frappe.throw("P2P plan steps cannot be deleted")
