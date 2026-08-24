"""Synora Phase 1 确定性主数据 cleanup（P1.2 / Inc-2）。

仅删除 seed.py 创建的命名空间数据（SYNORA-P1 前缀 / 命名空间公司），
删除顺序为创建顺序的逆序；环境基础（UOM Unit、Item Group root）保留。
只用标准 API frappe.delete_doc（触发上游 on_trash 级联：Company 清理
科目/成本中心/默认仓库，Supplier 清理关联 Contact/Address），不用 SQL、
frappe.db.delete、ignore_permissions。销毁整个 site 仅作最后手段（env.sh reset）。

容器内用法（env.sh cleanup 已封装）：
  exec(open("/tmp/synora_seed/cleanup.py").read()); run())
"""

import frappe

# ---- 确定性标识（与 seed.py 保持同步）----
PREFIX = "SYNORA-P1"
COMPANY = "SYNORA-P1 Test Company"
ITEM_GROUP = "SYNORA-P1 Items"
SUPPLIER = "SYNORA-P1-Supplier-1"
ITEM = "SYNORA-P1-Item-1001"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
FISCAL_YEAR = "SYNORA-P1 FY 2026"
BUYING_PRICE_LIST = "SYNORA-P1 Buying CNY"


def _delete(doctype, name):
    if not frappe.db.exists(doctype, name):
        print(f"[cleanup] absent  {doctype}: {name}")
        return
    frappe.delete_doc(doctype, name)
    print(f"[cleanup] deleted {doctype}: {name}")


def _cleanup():
    # 依赖安全序：Item 先于其 Item Group；自建 Warehouse 先于 Company（公司级联再清默认仓）
    _delete("Price List", BUYING_PRICE_LIST)
    _delete("Item", ITEM)
    _delete("Item Group", ITEM_GROUP)
    _delete("Supplier", SUPPLIER)
    _delete("Warehouse", WAREHOUSE)
    _delete("Fiscal Year", FISCAL_YEAR)  # 存在交易单据时会被 link check 阻止（fail closed）
    _delete("Company", COMPANY)  # 上游 on_trash 级联清默认仓库/科目/成本中心

    like = ("like", f"{PREFIX}%")
    leftover = {
        "Company": frappe.db.count("Company", {"name": COMPANY}),
        "Supplier": frappe.db.count("Supplier", {"name": like}),
        "Item": frappe.db.count("Item", {"name": like}),
        "Item Group(ns)": frappe.db.count("Item Group", {"name": like}),
        "Warehouse(ns)": frappe.db.count("Warehouse", {"name": like}),
        "Fiscal Year(ns)": frappe.db.count("Fiscal Year", {"name": like}),
        "Price List(ns)": frappe.db.count("Price List", {"name": like}),
        "Contact(ns)": frappe.db.count("Contact", {"first_name": like}),
    }
    print(f"[cleanup] leftover_counts = {leftover}")
    if any(leftover.values()):
        raise Exception(f"[cleanup] 命名空间内仍有残留，拒绝宣布清理完成: {leftover}")


def run():
    try:
        _cleanup()
        frappe.db.commit()
        print("CLEANUP-OK")
    except Exception:
        frappe.db.rollback()
        raise
