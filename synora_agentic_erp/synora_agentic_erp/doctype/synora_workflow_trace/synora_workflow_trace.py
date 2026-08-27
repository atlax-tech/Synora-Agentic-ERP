import frappe
from frappe.model.document import Document


class SynoraWorkflowTrace(Document):  # type: ignore[misc]
    def validate(self) -> None:
        if not self.is_new():
            frappe.throw("Synora workflow trace records are immutable")
