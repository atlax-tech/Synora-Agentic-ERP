from decimal import Decimal

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate

from synora_agentic_erp.api import cancel_run, execute, issue_run
from synora_agentic_erp.gateway.registry import _TOOLS

BUYER = "synora-p1-buyer@dev.localhost"
ACCOUNTANT = "synora-p1-accountant@dev.localhost"
COMPANY = "SYNORA-P1 Test Company"
GOAL = "ensure stock for SYNORA-P1-Item-1001 for the next quarter"
ITEM = "SYNORA-P1-Item-1001"
SUPPLIER = "SYNORA-P1-Supplier-1"
WAREHOUSE = "SYNORA-P1 Stores - SP1"
OTHER_WAREHOUSE = "SYNORA-P2 Other Stores - SP1"
CORRELATION_ID = "77d33e2d-6de9-4f0b-bf4c-b86d95f307b9"


class TestReadTools(FrappeTestCase):
    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        super().tearDown()

    def _issue(self, user: str = BUYER, warehouse: str | None = WAREHOUSE) -> dict[str, object]:
        frappe.set_user(user)
        response = issue_run(COMPANY, GOAL, warehouse, correlation_id=CORRELATION_ID)
        self.assertTrue(response["ok"])
        return response["run"]

    def _call(
        self, run: dict[str, object], name: str, tool_input: dict[str, object]
    ) -> dict[str, object]:
        frappe.set_user("Guest")
        return execute(
            schema_version="1",
            run_id=run["run_id"],
            capability=run["capability"],
            correlation_id=CORRELATION_ID,
            tool={"name": name, "version": "1", "input": tool_input},
        )

    def _create_other_warehouse(self) -> str:
        if frappe.db.exists("Warehouse", OTHER_WAREHOUSE):
            return OTHER_WAREHOUSE
        frappe.set_user("Administrator")
        warehouse = frappe.get_doc(
            {
                "doctype": "Warehouse",
                "warehouse_name": "SYNORA-P2 Other Stores",
                "company": COMPANY,
                "parent_warehouse": "All Warehouses - SP1",
            }
        ).insert()
        return warehouse.name

    def _create_item(
        self,
        item_code: str,
        stock_uom: str,
        purchase_uom: str | None = None,
        conversion_factor: float | None = None,
    ) -> str:
        if frappe.db.exists("Item", item_code):
            return item_code
        frappe.set_user("Administrator")
        values = {
            "doctype": "Item",
            "item_code": item_code,
            "item_name": item_code,
            "item_group": "Products",
            "stock_uom": stock_uom,
            "is_stock_item": 1,
        }
        if purchase_uom and conversion_factor:
            values["purchase_uom"] = purchase_uom
            values["uoms"] = [{"uom": purchase_uom, "conversion_factor": conversion_factor}]
        item = frappe.get_doc(values).insert()
        return item.name

    def _create_open_material_request(self, warehouse: str = WAREHOUSE) -> str:
        frappe.set_user(BUYER)
        request = frappe.get_doc(
            {
                "doctype": "Material Request",
                "naming_series": "MAT-MR-.YYYY.-",
                "material_request_type": "Purchase",
                "company": COMPANY,
                "transaction_date": getdate("2026-08-25"),
                "items": [
                    {
                        "item_code": ITEM,
                        "qty": 3,
                        "warehouse": warehouse,
                        "schedule_date": getdate("2026-08-30"),
                    }
                ],
            }
        ).insert()
        request.submit()
        return request.name

    def _create_open_purchase_order(
        self, warehouse: str = WAREHOUSE, supplier: str = SUPPLIER
    ) -> str:
        frappe.set_user(BUYER)
        order = frappe.get_doc(
            {
                "doctype": "Purchase Order",
                "company": COMPANY,
                "supplier": supplier,
                "transaction_date": getdate("2026-08-25"),
                "schedule_date": getdate("2026-08-30"),
                "items": [
                    {
                        "item_code": ITEM,
                        "qty": 2,
                        "rate": 100,
                        "warehouse": warehouse,
                        "schedule_date": getdate("2026-08-30"),
                    }
                ],
            }
        ).insert()
        order.submit()
        return order.name

    def _create_draft_purchase_order(self, warehouse: str = WAREHOUSE) -> str:
        frappe.set_user(BUYER)
        order = frappe.get_doc(
            {
                "doctype": "Purchase Order",
                "company": COMPANY,
                "supplier": SUPPLIER,
                "transaction_date": getdate("2026-08-25"),
                "schedule_date": getdate("2026-08-30"),
                "items": [
                    {
                        "item_code": ITEM,
                        "qty": 2,
                        "rate": 100,
                        "warehouse": warehouse,
                        "schedule_date": getdate("2026-08-30"),
                    }
                ],
            }
        ).insert()
        return order.name

    def _create_disabled_supplier_with_open_order(self) -> tuple[str, str, str]:
        frappe.set_user("Administrator")
        supplier = frappe.get_doc(
            {
                "doctype": "Supplier",
                "supplier_name": "SYNORA-P2 Disabled Supplier",
                "supplier_group": "All Supplier Groups",
                "supplier_type": "Company",
            }
        ).insert()
        order_name = self._create_open_purchase_order(supplier=supplier.name)
        order_modified = str(frappe.get_value("Purchase Order", order_name, "modified"))
        frappe.set_user("Administrator")
        supplier.disabled = 1
        supplier.save()
        return supplier.name, order_name, order_modified

    def test_item_supplier_and_projected_stock_use_real_erp_data(self) -> None:
        run = self._issue()
        item = self._call(run, "item.lookup", {"query": ITEM})
        self.assertEqual(item["data"][0]["item_code"], ITEM)

        supplier = self._call(run, "supplier.lookup", {"query": SUPPLIER})
        self.assertEqual(supplier["data"][0]["supplier"], SUPPLIER)

        stock = self._call(
            run,
            "stock.projected",
            {"item_code": ITEM, "warehouse": WAREHOUSE},
        )
        self.assertTrue(stock["ok"])
        self.assertEqual(stock["authorized_scope"]["warehouse"], WAREHOUSE)
        self.assertEqual(stock["data"][0]["item_code"], ITEM)
        self.assertIn("projected_qty", stock["data"][0])

    def test_open_demand_and_material_request_follow_outstanding_quantity(self) -> None:
        run = self._issue()
        before = self._call(
            run,
            "demand.open",
            {"item_code": ITEM, "warehouse": WAREHOUSE},
        )
        before_row = before["data"][0] if before["data"] else None
        before_qty = before_row["open_stock_qty"] if before_row else 0.0
        before_count = before_row["material_request_count"] if before_row else 0

        request_name = self._create_open_material_request()

        requests = self._call(run, "material_request.open", {})
        self.assertIn(request_name, {row["material_request"] for row in requests["data"]})

        demand = self._call(
            run,
            "demand.open",
            {"item_code": ITEM, "warehouse": WAREHOUSE},
        )
        self.assertEqual(demand["data"][0]["item_code"], ITEM)
        self.assertEqual(Decimal(demand["data"][0]["open_stock_qty"]), Decimal(str(before_qty)) + 3)
        self.assertEqual(demand["data"][0]["material_request_count"], before_count + 1)
        self.assertLessEqual(demand["data"][0]["earliest_schedule_date"], "2026-08-30")

    def test_open_purchase_order_uses_submitted_erp_status(self) -> None:
        order_name = self._create_open_purchase_order()
        run = self._issue()
        orders = self._call(run, "purchase_order.open", {"supplier": SUPPLIER})

        result = next(row for row in orders["data"] if row["purchase_order"] == order_name)
        self.assertEqual(result["status"], "To Receive and Bill")
        self.assertEqual(result["currency"], "CNY")

    def test_current_material_request_reads_live_fields_and_is_non_mutating(self) -> None:
        request_name = self._create_open_material_request()
        before = frappe.db.get_value(
            "Material Request", request_name, ["docstatus", "status", "modified"]
        )
        run = self._issue()

        response = self._call(run, "material_request.current", {"name": request_name})

        self.assertTrue(response["ok"])
        self.assertEqual(response["tool"]["name"], "material_request.current")
        self.assertEqual(response["data"][0]["material_request"], request_name)
        self.assertEqual(response["data"][0]["company"], COMPANY)
        self.assertEqual(response["data"][0]["docstatus"], 1)
        self.assertEqual(Decimal(response["data"][0]["requested_stock_qty"]), Decimal("3"))
        self.assertEqual(Decimal(response["data"][0]["ordered_stock_qty"]), Decimal("0"))
        self.assertEqual(Decimal(response["data"][0]["open_order_stock_qty"]), Decimal("3"))
        self.assertTrue(response["snapshot"]["captured_at"])
        self.assertTrue(response["snapshot"]["source_modified_at"])
        after = frappe.db.get_value(
            "Material Request", request_name, ["docstatus", "status", "modified"]
        )
        self.assertEqual(after, before)

    def test_current_purchase_order_reads_live_fields_including_draft(self) -> None:
        order_name = self._create_draft_purchase_order()
        before = frappe.db.get_value(
            "Purchase Order", order_name, ["docstatus", "status", "modified"]
        )
        run = self._issue()

        response = self._call(run, "purchase_order.current", {"name": order_name})

        self.assertTrue(response["ok"])
        self.assertEqual(response["tool"]["name"], "purchase_order.current")
        self.assertEqual(response["data"][0]["purchase_order"], order_name)
        self.assertEqual(response["data"][0]["supplier"], SUPPLIER)
        self.assertEqual(response["data"][0]["currency"], "CNY")
        self.assertEqual(response["data"][0]["docstatus"], 0)
        self.assertEqual(response["data"][0]["status"], "Draft")
        self.assertEqual(Decimal(response["data"][0]["ordered_stock_qty"]), Decimal("2"))
        self.assertEqual(Decimal(response["data"][0]["received_stock_qty"]), Decimal("0"))
        self.assertEqual(Decimal(response["data"][0]["open_receipt_stock_qty"]), Decimal("2"))
        after = frappe.db.get_value(
            "Purchase Order", order_name, ["docstatus", "status", "modified"]
        )
        self.assertEqual(after, before)

    def test_current_read_preserves_cancelled_document_state(self) -> None:
        order_name = self._create_open_purchase_order()
        frappe.set_user(BUYER)
        frappe.get_doc("Purchase Order", order_name).cancel()
        run = self._issue()

        response = self._call(run, "purchase_order.current", {"name": order_name})

        self.assertEqual(response["data"][0]["docstatus"], 2)
        self.assertEqual(response["data"][0]["status"], "Cancelled")

    def test_current_reads_reject_cancelled_run_before_handler(self) -> None:
        request_name = self._create_open_material_request()
        order_name = self._create_draft_purchase_order()
        run = self._issue()

        frappe.set_user(BUYER)
        cancelled = cancel_run(str(run["run_id"]), CORRELATION_ID)
        self.assertTrue(cancelled["ok"])
        self.assertEqual(cancelled["run"]["run_state"], "CANCELLED")

        for tool_name, name in (
            ("material_request.current", request_name),
            ("purchase_order.current", order_name),
        ):
            response = self._call(run, tool_name, {"name": name})
            self.assertEqual(response["error"]["code"], "RUN_REJECTED")

    def test_accountant_cannot_gain_purchase_order_read_permission(self) -> None:
        run = self._issue(ACCOUNTANT, warehouse=None)
        response = self._call(run, "purchase_order.open", {})
        self.assertEqual(response["error"]["code"], "PERMISSION_DENIED")
        response = self._call(run, "purchase_order.current", {"name": "PUR-ORD-NOT-A-REAL-ID"})
        self.assertEqual(response["error"]["code"], "PERMISSION_DENIED")

    def test_tool_scope_and_pagination_fail_closed(self) -> None:
        run = self._issue()
        response = self._call(
            run,
            "stock.projected",
            {"item_code": ITEM, "warehouse": "All Warehouses - SP1"},
        )
        self.assertEqual(response["error"]["code"], "SCOPE_DENIED")

        response = self._call(run, "item.lookup", {"limit": 51})
        self.assertEqual(response["error"]["code"], "INVALID_INPUT")

        for name in ("material_request.current", "purchase_order.current"):
            response = self._call(run, name, {"name": " "})
            self.assertEqual(response["error"]["code"], "INVALID_INPUT")
            response = self._call(run, name, {"name": "x" * 141})
            self.assertEqual(response["error"]["code"], "INVALID_INPUT")

    def test_warehouse_scoped_run_excludes_other_warehouse_mr_and_po(self) -> None:
        run = self._issue()
        before_requests = self._call(run, "material_request.open", {})
        before_orders = self._call(run, "purchase_order.open", {})
        self.assertEqual(self._create_other_warehouse(), OTHER_WAREHOUSE)
        other_request = self._create_open_material_request(OTHER_WAREHOUSE)
        other_order = self._create_open_purchase_order(OTHER_WAREHOUSE)

        requests = self._call(run, "material_request.open", {})
        self.assertNotIn(other_request, {row["material_request"] for row in requests["data"]})
        self.assertEqual(
            requests["snapshot"]["source_modified_at"],
            before_requests["snapshot"]["source_modified_at"],
        )

        orders = self._call(run, "purchase_order.open", {})
        self.assertNotIn(other_order, {row["purchase_order"] for row in orders["data"]})
        self.assertEqual(
            orders["snapshot"]["source_modified_at"],
            before_orders["snapshot"]["source_modified_at"],
        )

    def test_current_read_is_opaque_for_missing_or_other_warehouse_documents(self) -> None:
        self.assertEqual(self._create_other_warehouse(), OTHER_WAREHOUSE)
        other_request = self._create_open_material_request(OTHER_WAREHOUSE)
        run = self._issue()

        for name in ("MAT-MR-NOT-A-REAL-ID", other_request):
            response = self._call(run, "material_request.current", {"name": name})
            self.assertEqual(response["error"]["code"], "NOT_FOUND")
            self.assertEqual(response["error"]["message"], "requested resource is not available")

    def test_current_warehouse_scope_excludes_other_lines_without_aggregates(self) -> None:
        self.assertEqual(self._create_other_warehouse(), OTHER_WAREHOUSE)
        other_item = self._create_item("SYNORA-P2-Other-Warehouse-Item", "Unit")
        frappe.set_user(BUYER)
        request = frappe.get_doc(
            {
                "doctype": "Material Request",
                "material_request_type": "Purchase",
                "company": COMPANY,
                "transaction_date": getdate("2026-08-25"),
                "items": [
                    {
                        "item_code": other_item,
                        "qty": 3,
                        "warehouse": WAREHOUSE,
                        "schedule_date": getdate("2026-08-30"),
                    },
                    {
                        "item_code": ITEM,
                        "qty": 99,
                        "warehouse": OTHER_WAREHOUSE,
                        "schedule_date": getdate("2026-08-30"),
                    },
                ],
            }
        ).insert()
        request.submit()
        run = self._issue()

        response = self._call(run, "material_request.current", {"name": request.name})

        self.assertEqual(len(response["data"]), 1)
        self.assertEqual(response["data"][0]["warehouse"], WAREHOUSE)
        self.assertEqual(Decimal(response["data"][0]["requested_stock_qty"]), Decimal("3"))
        self.assertNotIn(OTHER_WAREHOUSE, str(response))
        self.assertNotEqual(response["data"][0]["requested_stock_qty"], "99")

        frappe.set_user(BUYER)
        order = frappe.get_doc(
            {
                "doctype": "Purchase Order",
                "company": COMPANY,
                "supplier": SUPPLIER,
                "transaction_date": getdate("2026-08-25"),
                "schedule_date": getdate("2026-08-30"),
                "items": [
                    {
                        "item_code": ITEM,
                        "qty": 2,
                        "rate": 100,
                        "warehouse": WAREHOUSE,
                        "schedule_date": getdate("2026-08-30"),
                    },
                    {
                        "item_code": other_item,
                        "qty": 99,
                        "rate": 100,
                        "warehouse": OTHER_WAREHOUSE,
                        "schedule_date": getdate("2026-08-30"),
                    },
                ],
            }
        ).insert()
        order.submit()
        po_response = self._call(run, "purchase_order.current", {"name": order.name})
        self.assertEqual(len(po_response["data"]), 1)
        self.assertEqual(po_response["data"][0]["warehouse"], WAREHOUSE)
        self.assertEqual(Decimal(po_response["data"][0]["ordered_stock_qty"]), Decimal("2"))
        self.assertNotIn(OTHER_WAREHOUSE, str(po_response))

    def test_open_documents_keep_mixed_stock_units_separate(self) -> None:
        unit_item = self._create_item("SYNORA-P2-Mixed-Unit", "Unit")
        kg_item = self._create_item("SYNORA-P2-Mixed-Kg", "Kg", "Gram", 0.001)
        frappe.set_user(BUYER)
        request = frappe.get_doc(
            {
                "doctype": "Material Request",
                "material_request_type": "Purchase",
                "company": COMPANY,
                "transaction_date": getdate("2026-08-25"),
                "items": [
                    {
                        "item_code": unit_item,
                        "qty": 2,
                        "warehouse": WAREHOUSE,
                        "schedule_date": getdate("2026-08-30"),
                    },
                    {
                        "item_code": kg_item,
                        "qty": 3.5,
                        "warehouse": WAREHOUSE,
                        "schedule_date": getdate("2026-08-30"),
                    },
                ],
            }
        ).insert()
        request.submit()
        order = frappe.get_doc(
            {
                "doctype": "Purchase Order",
                "company": COMPANY,
                "supplier": SUPPLIER,
                "transaction_date": getdate("2026-08-25"),
                "schedule_date": getdate("2026-08-30"),
                "items": [
                    {
                        "item_code": unit_item,
                        "qty": 2,
                        "rate": 10,
                        "warehouse": WAREHOUSE,
                        "schedule_date": getdate("2026-08-30"),
                    },
                    {
                        "item_code": kg_item,
                        "qty": 3500,
                        "uom": "Gram",
                        "conversion_factor": 0.001,
                        "rate": 20,
                        "warehouse": WAREHOUSE,
                        "schedule_date": getdate("2026-08-30"),
                    },
                ],
            }
        ).insert()
        order.submit()
        run = self._issue()

        requests = self._call(run, "material_request.open", {})
        request_rows = [row for row in requests["data"] if row["material_request"] == request.name]
        self.assertEqual({row["stock_uom"] for row in request_rows}, {"Unit", "Kg"})

        orders = self._call(run, "purchase_order.open", {})
        order_rows = [row for row in orders["data"] if row["purchase_order"] == order.name]
        self.assertEqual({row["stock_uom"] for row in order_rows}, {"Unit", "Kg"})
        kg_row = next(row for row in order_rows if row["stock_uom"] == "Kg")
        self.assertEqual(Decimal(kg_row["open_receipt_qty"]), Decimal("3.5"))

    def test_disabled_supplier_order_is_explicitly_omitted(self) -> None:
        _, order_name, order_modified = self._create_disabled_supplier_with_open_order()
        run = self._issue()

        orders = self._call(run, "purchase_order.open", {})

        self.assertEqual(orders["completeness"]["status"], "PARTIAL")
        self.assertGreaterEqual(
            orders["completeness"]["omissions"]["inactive_supplier_documents"], 1
        )
        self.assertNotIn(order_name, {row["purchase_order"] for row in orders["data"]})
        self.assertNotEqual(orders["snapshot"]["source_modified_at"], order_modified)

    def test_open_document_tools_declare_all_permission_dependencies(self) -> None:
        self.assertEqual(
            _TOOLS[("material_request.open", "1")].required_doctypes,
            ("Item", "Warehouse", "Material Request"),
        )
        self.assertEqual(
            _TOOLS[("purchase_order.open", "1")].required_doctypes,
            ("Item", "Supplier", "Warehouse", "Purchase Order"),
        )
        self.assertEqual(
            _TOOLS[("material_request.current", "1")].required_doctypes,
            ("Material Request",),
        )
        self.assertEqual(
            _TOOLS[("purchase_order.current", "1")].required_doctypes,
            ("Purchase Order",),
        )
