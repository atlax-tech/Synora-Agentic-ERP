import frappe
from frappe.model.document import Document


class SynoraWorkflowToolInvocation(Document):  # type: ignore[misc]
    def validate(self) -> None:
        if self.is_new():
            return
        if not self.flags.synora_invocation_completion:
            frappe.throw("Synora workflow invocation records are immutable")
        changed = [
            field
            for field in (
                "invocation_id",
                "run",
                "initiator",
                "plan_version",
                "step_id",
                "tool_name",
                "tool_version",
                "args_digest",
                "started_at",
                "correlation_id",
            )
            if self.has_value_changed(field)
        ]
        if changed:
            frappe.throw("workflow invocation identity is immutable")
        if self.status != "SUCCEEDED":
            frappe.throw("workflow invocation completion is invalid")
