"""Phase 2 P2.6 边界数据准备（在 bench console 内执行）。

幂等地准备 P2.6 端到端验证所需的边界数据，并在每次运行前清理旧数据：
- 第二家公司 SYNORA-P26 Test Company（ADR-0003 要求的跨公司 fixture）
  + 其 Warehouse / Supplier / 开放 PO，用于跨公司数据隔离验证；
- 停用供应商 + 其已提交开放 PO（验证 disabled supplier 显式省略）；
- 已取消的 Material Request（验证 Cancelled 单据不计入 open demand/MR）；
- 5 个轻量 Item（验证 item.lookup 分页）。

成功标记：P26-DATA-OK。重复运行安全（先清理后创建）。
"""
import frappe
from frappe.utils import getdate

BUYER = "synora-p1-buyer@dev.localhost"
COMPANY_A = "SYNORA-P1 Test Company"
COMPANY_B = "SYNORA-P26 Test Company"
ABBR_B = "P26"
ITEM = "SYNORA-P1-Item-1001"
SUPPLIER_B = "SYNORA-P26-Supplier-1"
WAREHOUSE_B = "SYNORA-P26 Stores - P26"
DISABLED_SUPPLIER = "SYNORA-P26-Disabled-Supplier"


def _delete_documents(doctype: str, filters: dict[str, object]) -> None:
    for name in frappe.get_all(doctype, pluck="name", filters=filters):
        doc = frappe.get_doc(doctype, name)
        if doc.docstatus == 1:
            doc.cancel()
        frappe.delete_doc(doctype, name, force=True)


def _cleanup() -> None:
    """删除上一次运行残留的 P26 命名空间数据，保证幂等。"""
    frappe.set_user("Administrator")
    _delete_documents("Purchase Order", {"company": COMPANY_B})
    _delete_documents("Purchase Order", {"supplier": DISABLED_SUPPLIER})
    _delete_documents(
        "Material Request",
        {"material_request_type": "Purchase", "owner": BUYER, "docstatus": 2},
    )
    for code in frappe.get_all(
        "Item", pluck="name", filters={"item_code": ["like", "SYNORA-P26-%"]}
    ):
        frappe.delete_doc("Item", code, force=True)
    for name in frappe.get_all(
        "Supplier", pluck="name", filters={"name": ["in", [DISABLED_SUPPLIER, SUPPLIER_B]]}
    ):
        frappe.delete_doc("Supplier", name, force=True)
    if frappe.db.exists("Company", COMPANY_B):
        frappe.delete_doc("Company", COMPANY_B, force=True)
    frappe.db.commit()


def _ensure_company_b() -> str:
    frappe.set_user("Administrator")
    if not frappe.db.exists("Company", COMPANY_B):
        frappe.get_doc(
            {
                "doctype": "Company",
                "company_name": COMPANY_B,
                "abbr": ABBR_B,
                "default_currency": "CNY",
                "country": "China",
            }
        ).insert()
    if not frappe.db.exists("Supplier", SUPPLIER_B):
        frappe.get_doc(
            {
                "doctype": "Supplier",
                "supplier_name": "SYNORA-P26-Supplier-1",
                "supplier_group": "All Supplier Groups",
                "supplier_type": "Company",
            }
        ).insert()
    warehouse = frappe.get_doc(
        {
            "doctype": "Warehouse",
            "warehouse_name": "SYNORA-P26 Stores",
            "company": COMPANY_B,
            "parent_warehouse": f"All Warehouses - {ABBR_B}",
        }
    ).insert()
    return warehouse.name


def _open_po(company: str, supplier: str, warehouse: str) -> str:
    frappe.set_user(BUYER)
    order = frappe.get_doc(
        {
            "doctype": "Purchase Order",
            "company": company,
            "supplier": supplier,
            "transaction_date": getdate("2026-08-25"),
            "schedule_date": getdate("2026-08-30"),
            "items": [
                {
                    "item_code": ITEM,
                    "qty": 1,
                    "rate": 10,
                    "warehouse": warehouse,
                    "schedule_date": getdate("2026-08-30"),
                }
            ],
        }
    ).insert()
    order.submit()
    return order.name


def _upsert_disabled_supplier_order() -> str:
    frappe.set_user("Administrator")
    supplier = frappe.get_doc(
        {
            "doctype": "Supplier",
            "supplier_name": "SYNORA-P26-Disabled-Supplier",
            "supplier_group": "All Supplier Groups",
            "supplier_type": "Company",
        }
    ).insert()
    order_name = _open_po(COMPANY_A, supplier.name, "SYNORA-P1 Stores - SP1")
    frappe.set_user("Administrator")
    supplier.disabled = 1
    supplier.save()
    return order_name


def _create_cancelled_material_request() -> str:
    frappe.set_user(BUYER)
    request = frappe.get_doc(
        {
            "doctype": "Material Request",
            "naming_series": "MAT-MR-.YYYY.-",
            "material_request_type": "Purchase",
            "company": COMPANY_A,
            "transaction_date": getdate("2026-08-25"),
            "items": [
                {
                    "item_code": ITEM,
                    "qty": 5,
                    "warehouse": "SYNORA-P1 Stores - SP1",
                    "schedule_date": getdate("2026-08-30"),
                }
            ],
        }
    ).insert()
    request.submit()
    request.cancel()
    return request.name


def _create_paging_items() -> list[str]:
    frappe.set_user("Administrator")
    created: list[str] = []
    for number in range(1, 6):
        code = f"SYNORA-P26-Item-{number}"
        if not frappe.db.exists("Item", code):
            item = frappe.get_doc(
                {
                    "doctype": "Item",
                    "item_code": code,
                    "item_name": code,
                    "item_group": "Products",
                    "stock_uom": "Unit",
                    "is_stock_item": 0,
                }
            ).insert()
            created.append(code)
    return created


def run() -> None:
    _cleanup()
    company_b_warehouse = _ensure_company_b()
    order_b = _open_po(COMPANY_B, SUPPLIER_B, company_b_warehouse)
    disabled_order = _upsert_disabled_supplier_order()
    cancelled_mr = _create_cancelled_material_request()
    paging_items = _create_paging_items()
    frappe.db.commit()
    print(
        "P26-DATA-OK "
        f"company_b={COMPANY_B} order_b={order_b} "
        f"disabled_order={disabled_order} cancelled_mr={cancelled_mr} "
        f"paging_items={len(paging_items)}"
    )


run()
