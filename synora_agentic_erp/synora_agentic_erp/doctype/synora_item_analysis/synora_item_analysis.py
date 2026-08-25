import frappe
from frappe.model.document import Document


class SynoraItemAnalysis(Document):  # type: ignore[misc]
    def validate(self) -> None:
        if self.is_new():
            return
        # 分析结果是确定性计算的只读快照, 不允许事后修改。
        frappe.throw("Synora Item Analysis is immutable")
