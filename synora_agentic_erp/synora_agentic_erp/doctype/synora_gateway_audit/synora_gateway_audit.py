import frappe
from frappe.model.document import Document


class SynoraGatewayAudit(Document):  # type: ignore[misc]
    def validate(self) -> None:
        if not self.is_new():
            frappe.throw("Synora Gateway Audit records are immutable")
