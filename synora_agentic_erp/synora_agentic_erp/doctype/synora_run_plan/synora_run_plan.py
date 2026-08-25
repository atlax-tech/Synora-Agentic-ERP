import frappe
from frappe.model.document import Document


class SynoraRunPlan(Document):  # type: ignore[misc]
    def validate(self) -> None:
        if self.is_new():
            return
        # 计划是确定性生成的可审查快照, 不允许事后修改。
        frappe.throw("Synora Run Plan is immutable")
