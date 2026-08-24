"""Synora Phase 1 确定性主数据 seed（P1.2 / Inc-2）。

仅用于专用 disposable site（FRAPPE_SITE）。命名空间锚点 = 公司名称，
其余对象统一 SYNORA-P1 前缀；幂等：存在即跳过。
只用标准 DocType API（frappe.get_doc / insert），不用 SQL、
frappe.db.delete、ignore_permissions。

上游事实（候选 SHA：erpnext 11e0ba0a / frappe 6a329d0，已取证）：
- Company autoname=field:company_name；on_update 自动创建默认仓库
  （root 名 "All Warehouses - {abbr}"）与科目表/成本中心，随公司删除级联清理。
- Supplier：site 实测 supp_master_name='Supplier Name'，name=supplier_name；
  不传 mobile_no/email_id 则不自动建 Contact。
- Item autoname=field:item_code；Item Group/UOM autoname=字段同名。
- Warehouse autoname = warehouse_name + " - " + company abbr。

容器内用法（env.sh seed 已封装）：
  exec(open("/tmp/synora_seed/seed.py").read()); run()
"""

import frappe

# ---- 确定性标识（与 cleanup.py 保持同步）----
PREFIX = "SYNORA-P1"
COMPANY = "SYNORA-P1 Test Company"
ABBR = "SP1"
ITEM_GROUP = "SYNORA-P1 Items"
ROOT_ITEM_GROUP = "All Item Groups"  # 环境基础，cleanup 保留
STOCK_UOM = "Unit"  # 环境基础，cleanup 保留
TRANSIT_WH_TYPE = "Transit"  # 环境基础：Company 建默认仓 Goods In Transit 时链接（autoname=Prompt，需显式 name）
SUPPLIER = "SYNORA-P1-Supplier-1"
ITEM = "SYNORA-P1-Item-1001"
WAREHOUSE_NAME = "SYNORA-P1 Stores"
WAREHOUSE = f"{WAREHOUSE_NAME} - {ABBR}"  # Warehouse autoname 追加公司缩写
ROOT_WAREHOUSE = f"All Warehouses - {ABBR}"


def _get_or_insert(doctype, name, values):
    if frappe.db.exists(doctype, name):
        print(f"[seed] exists  {doctype}: {name}")
        return
    frappe.get_doc(values).insert()
    frappe.db.commit()
    print(f"[seed] created {doctype}: {name}")


def namespace_counts():
    """命名空间口径计数：验收时两次 seed 后须一致，cleanup 后须全为 0。"""
    like = ("like", f"{PREFIX}%")
    return {
        "Company": frappe.db.count("Company", {"name": COMPANY}),
        "Supplier": frappe.db.count("Supplier", {"name": like}),
        "Item": frappe.db.count("Item", {"name": like}),
        "Item Group(ns)": frappe.db.count("Item Group", {"name": like}),
        "Warehouse(ns)": frappe.db.count("Warehouse", {"name": like}),
    }


def retained_counts():
    """环境基础（非命名空间，cleanup 保留）：供完整性对照。"""
    return {
        "UOM:Unit": 1 if frappe.db.exists("UOM", STOCK_UOM) else 0,
        "ItemGroup:root": 1 if frappe.db.exists("Item Group", ROOT_ITEM_GROUP) else 0,
        "WarehouseType:Transit": 1 if frappe.db.exists("Warehouse Type", TRANSIT_WH_TYPE) else 0,
    }


def run():
    # 环境基础（标准名，等同 setup wizard 产物，不属命名空间）
    _get_or_insert("UOM", STOCK_UOM, {"doctype": "UOM", "uom_name": STOCK_UOM})
    _get_or_insert("Item Group", ROOT_ITEM_GROUP,
                   {"doctype": "Item Group", "item_group_name": ROOT_ITEM_GROUP, "is_group": 1})
    _get_or_insert("Warehouse Type", TRANSIT_WH_TYPE,
                   {"doctype": "Warehouse Type", "warehouse_type": TRANSIT_WH_TYPE, "name": TRANSIT_WH_TYPE})

    # 命名空间数据，依赖顺序：公司 → 物料组 → 供应商 → 物料 → 仓库
    _get_or_insert("Company", COMPANY, {
        "doctype": "Company", "company_name": COMPANY, "abbr": ABBR,
        "default_currency": "CNY", "country": "China",
    })
    _get_or_insert("Item Group", ITEM_GROUP, {
        "doctype": "Item Group", "item_group_name": ITEM_GROUP,
        "parent_item_group": ROOT_ITEM_GROUP, "is_group": 0,
    })
    _get_or_insert("Supplier", SUPPLIER, {
        "doctype": "Supplier", "supplier_name": SUPPLIER,
    })
    _get_or_insert("Item", ITEM, {
        "doctype": "Item", "item_code": ITEM, "item_name": ITEM,
        "item_group": ITEM_GROUP, "stock_uom": STOCK_UOM,
    })
    _get_or_insert("Warehouse", WAREHOUSE, {
        "doctype": "Warehouse", "warehouse_name": WAREHOUSE_NAME,
        "company": COMPANY, "parent_warehouse": ROOT_WAREHOUSE,
    })

    print(f"[seed] namespace_counts = {namespace_counts()}")
    print(f"[seed] retained_counts  = {retained_counts()}")
    print("SEED-OK")
