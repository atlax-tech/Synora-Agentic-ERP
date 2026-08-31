"""Immutable, service-only Coach answer evidence for Runs history."""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.model.document import Document

SERVICE_FLAG = "synora_coach_result_service"
MAX_JSON_LENGTH = 256_000
STATUSES = frozenset({"ANSWERED", "CONFLICT", "UNKNOWN", "REFUSED"})
REFUSAL_REASONS = frozenset(
    {
        "Coach could not produce a grounded answer",
        "Coach declined to answer",
        "CONTEXT_REQUIRED",
    }
)


def _canonical_json(value: object, fieldname: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_JSON_LENGTH:
        frappe.throw(f"Coach result {fieldname} is invalid", frappe.ValidationError)
    raw = value if isinstance(value, str) else ""
    try:
        parsed = json.loads(raw)
        canonical = json.dumps(
            parsed, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as error:
        frappe.throw(f"Coach result {fieldname} is invalid", frappe.ValidationError)
        raise AssertionError from error
    if canonical != raw:
        frappe.throw(f"Coach result {fieldname} is not canonical", frappe.ValidationError)
    return raw


class SynoraCoachResult(Document):  # type: ignore[misc]
    def has_permission(
        self, permtype: str = "read", *, debug: bool = False, user: str | None = None
    ) -> bool:
        del debug, user
        if permtype in {"read", "select", "create", "write", "delete"}:
            return bool(self.flags.ignore_permissions)
        return True

    def validate(self) -> None:
        if not self.flags.get(SERVICE_FLAG):
            frappe.throw(
                "Coach results require the controlled Coach service", frappe.PermissionError
            )
        if not self.is_new():
            frappe.throw("Coach result records are immutable", frappe.ValidationError)
        if not self.run or not self.correlation_id or self.purpose != "ERP_COACH":
            frappe.throw("Coach result identity is required", frappe.ValidationError)
        if self.answer_status not in STATUSES:
            frappe.throw("Coach result status is invalid", frappe.ValidationError)
        if self.refusal_reason and self.refusal_reason not in REFUSAL_REASONS:
            frappe.throw("Coach result refusal reason is invalid", frappe.ValidationError)
        for fieldname in (
            "claim_records_json",
            "claims_json",
            "citations_json",
            "trace_json",
            "usage_json",
        ):
            _canonical_json(getattr(self, fieldname, None), fieldname)
        try:
            claim_records = json.loads(self.claim_records_json)
            claims = json.loads(self.claims_json)
            citations = json.loads(self.citations_json)
        except (TypeError, ValueError) as error:
            frappe.throw("Coach result evidence is invalid", frappe.ValidationError)
            raise AssertionError from error
        if not all(isinstance(value, list) for value in (claim_records, claims, citations)):
            frappe.throw("Coach result evidence must be arrays", frappe.ValidationError)
        if len(claim_records) != len(claims):
            frappe.throw("Coach result claims do not match claim records", frappe.ValidationError)
        if self.answer_status in {"UNKNOWN", "REFUSED"}:
            if self.answer or claims or citations or claim_records or not self.refusal_reason:
                frappe.throw("Non-answer Coach result contains answer data", frappe.ValidationError)
        elif not self.answer or not claims or not citations or self.refusal_reason:
            frappe.throw("Answer Coach result is incomplete", frappe.ValidationError)
        if self.current_doctype and not self.current_name:
            frappe.throw("Coach result document name is required", frappe.ValidationError)
        if self.current_name and self.current_doctype not in {"Material Request", "Purchase Order"}:
            frappe.throw("Coach result document type is invalid", frappe.ValidationError)
        if int(self.latency_ms or 0) < 0:
            frappe.throw("Coach result latency is invalid", frappe.ValidationError)

    def on_trash(self) -> None:
        frappe.throw("Coach result records cannot be deleted", frappe.PermissionError)


def has_permission(
    doc: Document | None, ptype: str = "read", user: str | None = None, **_: Any
) -> bool:
    """Keep native/API access closed; the authenticated Runs service is the reader."""
    del doc, user
    return ptype not in {"read", "select", "create", "write", "delete"}


__all__ = ["SERVICE_FLAG", "SynoraCoachResult", "has_permission"]
