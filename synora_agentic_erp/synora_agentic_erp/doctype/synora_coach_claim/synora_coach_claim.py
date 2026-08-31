"""Immutable, service-only Coach claim provenance record."""

from __future__ import annotations

import frappe
from frappe.model.document import Document

SERVICE_FLAG = "synora_coach_claim_service"
MAX_SOURCE_SNAPSHOT_LENGTH = 16_000


class SynoraCoachClaim(Document):  # type: ignore[misc]
    def validate(self) -> None:
        if not self.flags.get(SERVICE_FLAG):
            frappe.throw(
                "Coach claims require the controlled Coach service", frappe.PermissionError
            )
        if not self.is_new():
            frappe.throw("Coach claim records are immutable", frappe.ValidationError)
        for fieldname in (
            "run",
            "initiator",
            "company_scope",
            "claim_digest",
            "citation_digest",
            "source_revision",
            "source_snapshot",
            "dedupe_key",
        ):
            if not getattr(self, fieldname, None):
                frappe.throw(f"Coach claim field is required: {fieldname}")
        if len(str(self.source_snapshot)) > MAX_SOURCE_SNAPSHOT_LENGTH:
            frappe.throw("Coach claim source snapshot is too large")

    def on_trash(self) -> None:
        frappe.throw("Coach claim records cannot be deleted", frappe.PermissionError)
