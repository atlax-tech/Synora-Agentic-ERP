from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any, cast

import frappe

from synora_agentic_erp.gateway.contract import (
    GatewayFault,
    InputField,
    JsonScalar,
    ToolResult,
    bounded_text,
    optional_text,
)
from synora_agentic_erp.gateway.registry import register
from synora_agentic_erp.gateway.security import RunContext

MAX_SCOPE_WAREHOUSES = 1_000
MAX_OPEN_DOCUMENTS = 10_000
MAX_DEMAND_LINES = 50_000
MAX_CURRENT_LINES = 50


def _optional(label: str) -> InputField:
    return InputField(lambda value: optional_text(value, label))


def _required(label: str) -> InputField:
    return InputField(lambda value: bounded_text(value, label), required=True)


def _required_identifier(label: str) -> InputField:
    def parse(value: object) -> str:
        parsed = bounded_text(value, label)
        if not parsed.strip():
            raise GatewayFault("INVALID_INPUT", f"{label} is invalid")
        return parsed

    return InputField(parse, required=True)


def _page(tool_input: dict[str, JsonScalar]) -> tuple[int, int]:
    return cast(int, tool_input["offset"]), cast(int, tool_input["limit"])


def _latest_modified(rows: Iterable[dict[str, Any]]) -> str | None:
    modified = [str(row["modified"]) for row in rows if row.get("modified")]
    return max(modified) if modified else None


def _quantity(value: object) -> Decimal:
    return Decimal(str(value or 0))


def _quantity_text(value: Decimal) -> str:
    return format(value, "f")


def _nonnegative_quantity(value: object, label: str) -> Decimal:
    try:
        quantity = _quantity(value)
    except (InvalidOperation, ValueError) as error:
        raise GatewayFault("ERP_ERROR", f"{label} is invalid", 502) from error
    if not quantity.is_finite() or quantity < 0:
        raise GatewayFault("ERP_ERROR", f"{label} is invalid", 502)
    return quantity


def _source_text(value: object, label: str) -> str:
    if value is None:
        raise GatewayFault("ERP_ERROR", f"{label} is invalid", 502)
    text = str(value)
    if not text.strip():
        raise GatewayFault("ERP_ERROR", f"{label} is invalid", 502)
    return text


def _document_status(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise GatewayFault("ERP_ERROR", "document status is invalid", 502)
    try:
        status = int(value)
    except (TypeError, ValueError) as error:
        raise GatewayFault("ERP_ERROR", "document status is invalid", 502) from error
    if status not in {0, 1, 2}:
        raise GatewayFault("ERP_ERROR", "document status is invalid", 502)
    return status


def _current_parent(
    run: RunContext,
    doctype: str,
    name: str,
    fields: list[str],
) -> Any:
    """Load one current document only through the caller's Frappe scope."""

    rows = frappe.get_list(
        doctype,
        fields=fields,
        filters={"name": name, "company": run.company},
        user=run.initiator,
        limit=1,
    )
    if not rows:
        raise GatewayFault("NOT_FOUND", "requested resource is not available", 404)
    return rows[0]


def _current_parent_only_row(
    *,
    parent: Any,
    identifier: str,
    parent_fields: dict[str, JsonScalar],
) -> dict[str, JsonScalar]:
    return {
        identifier: str(parent.name),
        **parent_fields,
        "item_code": None,
        "warehouse": None,
        "stock_uom": None,
        "schedule_date": None,
    }


def _warehouse_names(run: RunContext, requested: str | None) -> list[str]:
    if run.warehouse:
        if requested and requested != run.warehouse:
            raise GatewayFault("SCOPE_DENIED", "requested resource is not available", 403)
        return [run.warehouse]
    filters: dict[str, Any] = {"company": run.company, "disabled": 0, "is_group": 0}
    if requested:
        filters["name"] = requested
    rows = frappe.get_list(
        "Warehouse",
        pluck="name",
        filters=filters,
        user=run.initiator,
        order_by="name asc",
        limit=MAX_SCOPE_WAREHOUSES + 1,
    )
    if len(rows) > MAX_SCOPE_WAREHOUSES:
        raise GatewayFault("RESULT_LIMIT", "warehouse scope is too large", 422)
    if requested and requested not in rows:
        raise GatewayFault("SCOPE_DENIED", "requested resource is not available", 403)
    return list(rows)


def _ensure_enabled_item(run: RunContext, item_code: str) -> None:
    allowed = frappe.get_list(
        "Item",
        pluck="name",
        filters={"name": item_code, "disabled": 0},
        user=run.initiator,
        limit=1,
    )
    if item_code not in allowed:
        raise GatewayFault("NOT_FOUND", "requested resource is not available", 404)


def _enabled_item_codes(run: RunContext, rows: Iterable[Any]) -> set[str]:
    codes = sorted({row.item_code for row in rows})
    if not codes:
        return set()
    return set(
        frappe.get_list(
            "Item",
            pluck="name",
            filters={"name": ["in", codes], "disabled": 0},
            user=run.initiator,
            limit=min(len(codes), MAX_DEMAND_LINES),
        )
    )


@register(
    name="item.lookup",
    version="1",
    required_doctypes=("Item",),
    input_fields={"query": _optional("query")},
)
def item_lookup(run: RunContext, tool_input: dict[str, JsonScalar]) -> ToolResult:
    offset, limit = _page(tool_input)
    query = cast(str | None, tool_input["query"])
    filters: dict[str, Any] = {"disabled": 0}
    or_filters = None
    if query:
        pattern = f"%{query}%"
        or_filters = {"item_code": ["like", pattern], "item_name": ["like", pattern]}
    rows = frappe.get_list(
        "Item",
        fields=["item_code", "item_name", "item_group", "stock_uom", "modified"],
        filters=filters,
        or_filters=or_filters,
        user=run.initiator,
        order_by="item_code asc",
        offset=offset,
        limit=limit + 1,
    )
    items = [
        {
            "item_code": row.item_code,
            "item_name": row.item_name,
            "item_group": row.item_group,
            "stock_uom": row.stock_uom,
        }
        for row in rows
    ]
    return ToolResult(items=items, source_modified_at=_latest_modified(rows))


@register(
    name="supplier.lookup",
    version="1",
    required_doctypes=("Supplier",),
    input_fields={"query": _optional("query")},
)
def supplier_lookup(run: RunContext, tool_input: dict[str, JsonScalar]) -> ToolResult:
    offset, limit = _page(tool_input)
    query = cast(str | None, tool_input["query"])
    filters: dict[str, Any] = {"disabled": 0}
    or_filters = None
    if query:
        pattern = f"%{query}%"
        or_filters = {"name": ["like", pattern], "supplier_name": ["like", pattern]}
    rows = frappe.get_list(
        "Supplier",
        fields=["name", "supplier_name", "supplier_group", "country", "modified"],
        filters=filters,
        or_filters=or_filters,
        user=run.initiator,
        order_by="name asc",
        offset=offset,
        limit=limit + 1,
    )
    items = [
        {
            "supplier": row.name,
            "supplier_name": row.supplier_name,
            "supplier_group": row.supplier_group,
            "country": row.country,
        }
        for row in rows
    ]
    return ToolResult(items=items, source_modified_at=_latest_modified(rows))


@register(
    name="stock.projected",
    version="1",
    required_doctypes=("Bin", "Item", "Warehouse"),
    input_fields={"item_code": _required("item_code"), "warehouse": _optional("warehouse")},
)
def projected_stock(run: RunContext, tool_input: dict[str, JsonScalar]) -> ToolResult:
    offset, limit = _page(tool_input)
    item_code = cast(str, tool_input["item_code"])
    requested_warehouse = cast(str | None, tool_input["warehouse"])
    _ensure_enabled_item(run, item_code)
    warehouses = _warehouse_names(run, requested_warehouse)
    if not warehouses:
        return ToolResult(items=[])
    rows = frappe.get_list(
        "Bin",
        fields=[
            "item_code",
            "warehouse",
            "actual_qty",
            "planned_qty",
            "indented_qty",
            "ordered_qty",
            "reserved_qty",
            "projected_qty",
            "modified",
        ],
        filters={"item_code": item_code, "warehouse": ["in", warehouses]},
        user=run.initiator,
        order_by="warehouse asc",
        offset=offset,
        limit=limit + 1,
    )
    items = [
        {
            "item_code": row.item_code,
            "warehouse": row.warehouse,
            "actual_qty": _quantity_text(_quantity(row.actual_qty)),
            "planned_qty": _quantity_text(_quantity(row.planned_qty)),
            "indented_qty": _quantity_text(_quantity(row.indented_qty)),
            "ordered_qty": _quantity_text(_quantity(row.ordered_qty)),
            "reserved_qty": _quantity_text(_quantity(row.reserved_qty)),
            "projected_qty": _quantity_text(_quantity(row.projected_qty)),
        }
        for row in rows
    ]
    return ToolResult(items=items, source_modified_at=_latest_modified(rows))


def _open_material_requests(run: RunContext, limit: int, offset: int = 0) -> list[Any]:
    return cast(
        list[Any],
        frappe.get_list(
            "Material Request",
            fields=[
                "name",
                "transaction_date",
                "schedule_date",
                "status",
                "per_ordered",
                "modified",
            ],
            filters={
                "company": run.company,
                "material_request_type": "Purchase",
                "docstatus": 1,
                "per_ordered": ["<", 99.99],
                "status": ["not in", ["Stopped", "Cancelled"]],
            },
            user=run.initiator,
            order_by="transaction_date asc, name asc",
            offset=offset,
            limit=limit,
        ),
    )


@register(
    name="material_request.open",
    version="1",
    required_doctypes=("Item", "Warehouse", "Material Request"),
    input_fields={},
)
def open_material_requests(run: RunContext, tool_input: dict[str, JsonScalar]) -> ToolResult:
    offset, limit = _page(tool_input)
    parents = _open_material_requests(run, MAX_OPEN_DOCUMENTS + 1)
    if len(parents) > MAX_OPEN_DOCUMENTS:
        raise GatewayFault("RESULT_LIMIT", "open material request source is too large", 422)
    if not parents:
        return ToolResult(items=[])
    filters: dict[str, Any] = {"parent": ["in", [row.name for row in parents]]}
    if run.warehouse:
        filters["warehouse"] = run.warehouse
    lines = frappe.get_list(
        "Material Request Item",
        fields=[
            "parent",
            "item_code",
            "warehouse",
            "stock_uom",
            "stock_qty",
            "ordered_qty",
            "modified",
        ],
        filters=filters,
        parent_doctype="Material Request",
        user=run.initiator,
        limit=MAX_DEMAND_LINES + 1,
    )
    if len(lines) > MAX_DEMAND_LINES:
        raise GatewayFault("RESULT_LIMIT", "open material request source is too large", 422)
    enabled_codes = _enabled_item_codes(run, lines)
    inactive_lines = sum(line.item_code not in enabled_codes for line in lines)
    quantities: dict[tuple[str, str, str, str], Decimal] = {}
    included_lines: list[Any] = []
    for line in lines:
        outstanding = _quantity(line.stock_qty) - _quantity(line.ordered_qty)
        if outstanding <= 0 or line.item_code not in enabled_codes:
            continue
        included_lines.append(line)
        key = (line.parent, line.item_code, line.warehouse, line.stock_uom)
        quantities[key] = quantities.get(key, Decimal()) + outstanding
    parent_by_name = {row.name: row for row in parents}
    items = []
    for key in sorted(quantities):
        parent_name, item_code, warehouse, stock_uom = key
        row = parent_by_name[parent_name]
        items.append(
            {
                "material_request": row.name,
                "transaction_date": str(row.transaction_date),
                "schedule_date": str(row.schedule_date),
                "status": row.status,
                "item_code": item_code,
                "warehouse": warehouse,
                "stock_uom": stock_uom,
                "open_stock_qty": _quantity_text(quantities[key]),
            }
        )
    included_parent_names = {key[0] for key in quantities}
    scoped_parents = [row for row in parents if row.name in included_parent_names]
    modified = _latest_modified([*scoped_parents, *included_lines])
    omissions = {"inactive_item_lines": inactive_lines} if inactive_lines else {}
    return ToolResult(
        items=items[offset : offset + limit + 1],
        source_modified_at=modified,
        omissions=omissions,
    )


@register(
    name="purchase_order.open",
    version="1",
    required_doctypes=("Item", "Supplier", "Warehouse", "Purchase Order"),
    input_fields={"supplier": _optional("supplier")},
)
def open_purchase_orders(run: RunContext, tool_input: dict[str, JsonScalar]) -> ToolResult:
    offset, limit = _page(tool_input)
    supplier = cast(str | None, tool_input["supplier"])
    filters: dict[str, Any] = {
        "company": run.company,
        "docstatus": 1,
        "status": ["in", ["To Receive and Bill", "To Receive", "To Bill"]],
    }
    if supplier:
        filters["supplier"] = supplier
    parents = frappe.get_list(
        "Purchase Order",
        fields=[
            "name",
            "supplier",
            "transaction_date",
            "schedule_date",
            "status",
            "currency",
            "modified",
        ],
        filters=filters,
        user=run.initiator,
        order_by="transaction_date asc, name asc",
        limit=MAX_OPEN_DOCUMENTS + 1,
    )
    if len(parents) > MAX_OPEN_DOCUMENTS:
        raise GatewayFault("RESULT_LIMIT", "open purchase order source is too large", 422)
    if not parents:
        return ToolResult(items=[])
    line_filters: dict[str, Any] = {"parent": ["in", [row.name for row in parents]]}
    if run.warehouse:
        line_filters["warehouse"] = run.warehouse
    lines = frappe.get_list(
        "Purchase Order Item",
        fields=[
            "parent",
            "item_code",
            "warehouse",
            "stock_uom",
            "qty",
            "received_qty",
            "conversion_factor",
            "modified",
        ],
        filters=line_filters,
        parent_doctype="Purchase Order",
        user=run.initiator,
        limit=MAX_DEMAND_LINES + 1,
    )
    if len(lines) > MAX_DEMAND_LINES:
        raise GatewayFault("RESULT_LIMIT", "open purchase order source is too large", 422)
    scoped_parent_names = {line.parent for line in lines}
    scoped_parents = [row for row in parents if row.name in scoped_parent_names]
    scoped_suppliers = {row.supplier for row in scoped_parents}
    enabled_suppliers = set(
        frappe.get_list(
            "Supplier",
            pluck="name",
            filters={"name": ["in", sorted(scoped_suppliers)], "disabled": 0},
            user=run.initiator,
            limit=MAX_OPEN_DOCUMENTS,
        )
    )
    enabled_codes = _enabled_item_codes(run, lines)
    inactive_supplier_documents = sum(
        row.supplier not in enabled_suppliers for row in scoped_parents
    )
    inactive_item_lines = sum(line.item_code not in enabled_codes for line in lines)
    receipt_quantities: dict[tuple[str, str, str, str], Decimal] = {}
    included_lines: list[Any] = []
    parent_by_name = {row.name: row for row in scoped_parents}
    for line in lines:
        if (
            line.item_code not in enabled_codes
            or parent_by_name[line.parent].supplier not in enabled_suppliers
        ):
            continue
        included_lines.append(line)
        outstanding = max(
            (_quantity(line.qty) - _quantity(line.received_qty))
            * _quantity(line.conversion_factor),
            Decimal(),
        )
        key = (line.parent, line.item_code, line.warehouse, line.stock_uom)
        receipt_quantities[key] = receipt_quantities.get(key, Decimal()) + outstanding
    items = []
    for key in sorted(receipt_quantities):
        parent_name, item_code, warehouse, stock_uom = key
        row = parent_by_name[parent_name]
        items.append(
            {
                "purchase_order": row.name,
                "supplier": row.supplier,
                "transaction_date": str(row.transaction_date),
                "schedule_date": str(row.schedule_date),
                "status": row.status,
                "currency": row.currency,
                "item_code": item_code,
                "warehouse": warehouse,
                "stock_uom": stock_uom,
                "open_receipt_qty": _quantity_text(receipt_quantities[key]),
            }
        )
    included_parent_names = {key[0] for key in receipt_quantities}
    eligible_parents = [row for row in scoped_parents if row.name in included_parent_names]
    modified = _latest_modified([*eligible_parents, *included_lines])
    omissions = {
        key: count
        for key, count in {
            "inactive_supplier_documents": inactive_supplier_documents,
            "inactive_item_lines": inactive_item_lines,
        }.items()
        if count
    }
    return ToolResult(
        items=items[offset : offset + limit + 1],
        source_modified_at=modified,
        omissions=omissions,
    )


@register(
    name="material_request.current",
    version="1",
    required_doctypes=("Material Request",),
    input_fields={"name": _required_identifier("name")},
)
def current_material_request(run: RunContext, tool_input: dict[str, JsonScalar]) -> ToolResult:
    """Read one current Material Request without the open-list filters."""

    offset, limit = _page(tool_input)
    name = cast(str, tool_input["name"])
    parent = _current_parent(
        run,
        "Material Request",
        name,
        [
            "name",
            "company",
            "material_request_type",
            "docstatus",
            "status",
            "transaction_date",
            "modified",
        ],
    )
    line_filters: dict[str, Any] = {"parent": name}
    if run.warehouse:
        line_filters["warehouse"] = run.warehouse
    lines = frappe.get_list(
        "Material Request Item",
        fields=[
            "parent",
            "item_code",
            "warehouse",
            "stock_uom",
            "stock_qty",
            "ordered_qty",
            "schedule_date",
            "modified",
        ],
        filters=line_filters,
        parent_doctype="Material Request",
        user=run.initiator,
        order_by="idx asc",
        limit=MAX_CURRENT_LINES + 1,
    )
    if len(lines) > MAX_CURRENT_LINES:
        raise GatewayFault("RESULT_LIMIT", "current material request is too large", 422)
    if run.warehouse and not lines:
        raise GatewayFault("NOT_FOUND", "requested resource is not available", 404)

    parent_fields: dict[str, JsonScalar] = {
        "company": _source_text(parent.company, "company"),
        "material_request_type": _source_text(
            parent.material_request_type, "material request type"
        ),
        "docstatus": _document_status(parent.docstatus),
        "status": _source_text(parent.status, "status"),
        "transaction_date": _source_text(parent.transaction_date, "transaction date"),
    }
    items: list[dict[str, JsonScalar]] = []
    for line in lines:
        requested = _nonnegative_quantity(line.stock_qty, "requested quantity")
        ordered = _nonnegative_quantity(line.ordered_qty, "ordered quantity")
        items.append(
            {
                "material_request": str(parent.name),
                **parent_fields,
                "item_code": _source_text(line.item_code, "item code"),
                "warehouse": _source_text(line.warehouse, "warehouse"),
                "stock_uom": _source_text(line.stock_uom, "stock UOM"),
                "schedule_date": _source_text(line.schedule_date, "schedule date"),
                "requested_stock_qty": _quantity_text(requested),
                "ordered_stock_qty": _quantity_text(ordered),
                "open_order_stock_qty": _quantity_text(max(requested - ordered, Decimal())),
            }
        )
    if not items:
        items.append(
            _current_parent_only_row(
                parent=parent,
                identifier="material_request",
                parent_fields=parent_fields,
            )
            | {
                "requested_stock_qty": None,
                "ordered_stock_qty": None,
                "open_order_stock_qty": None,
            }
        )
    return ToolResult(
        items=items[offset : offset + limit + 1],
        source_modified_at=_latest_modified([parent, *lines]),
    )


@register(
    name="purchase_order.current",
    version="1",
    required_doctypes=("Purchase Order",),
    input_fields={"name": _required_identifier("name")},
)
def current_purchase_order(run: RunContext, tool_input: dict[str, JsonScalar]) -> ToolResult:
    """Read one current Purchase Order without the open-list filters."""

    offset, limit = _page(tool_input)
    name = cast(str, tool_input["name"])
    parent = _current_parent(
        run,
        "Purchase Order",
        name,
        [
            "name",
            "company",
            "supplier",
            "docstatus",
            "status",
            "transaction_date",
            "currency",
            "modified",
        ],
    )
    line_filters: dict[str, Any] = {"parent": name}
    if run.warehouse:
        line_filters["warehouse"] = run.warehouse
    lines = frappe.get_list(
        "Purchase Order Item",
        fields=[
            "parent",
            "item_code",
            "warehouse",
            "stock_uom",
            "qty",
            "received_qty",
            "conversion_factor",
            "schedule_date",
            "modified",
        ],
        filters=line_filters,
        parent_doctype="Purchase Order",
        user=run.initiator,
        order_by="idx asc",
        limit=MAX_CURRENT_LINES + 1,
    )
    if len(lines) > MAX_CURRENT_LINES:
        raise GatewayFault("RESULT_LIMIT", "current purchase order is too large", 422)
    if run.warehouse and not lines:
        raise GatewayFault("NOT_FOUND", "requested resource is not available", 404)

    parent_fields: dict[str, JsonScalar] = {
        "company": _source_text(parent.company, "company"),
        "supplier": _source_text(parent.supplier, "supplier"),
        "docstatus": _document_status(parent.docstatus),
        "status": _source_text(parent.status, "status"),
        "transaction_date": _source_text(parent.transaction_date, "transaction date"),
        "currency": _source_text(parent.currency, "currency"),
    }
    items: list[dict[str, JsonScalar]] = []
    for line in lines:
        conversion = _nonnegative_quantity(line.conversion_factor, "conversion factor")
        if conversion == 0:
            raise GatewayFault("ERP_ERROR", "conversion factor is invalid", 502)
        ordered = _nonnegative_quantity(line.qty, "ordered quantity") * conversion
        received = _nonnegative_quantity(line.received_qty, "received quantity") * conversion
        items.append(
            {
                "purchase_order": str(parent.name),
                **parent_fields,
                "item_code": _source_text(line.item_code, "item code"),
                "warehouse": _source_text(line.warehouse, "warehouse"),
                "stock_uom": _source_text(line.stock_uom, "stock UOM"),
                "schedule_date": _source_text(line.schedule_date, "schedule date"),
                "ordered_stock_qty": _quantity_text(ordered),
                "received_stock_qty": _quantity_text(received),
                "open_receipt_stock_qty": _quantity_text(max(ordered - received, Decimal())),
            }
        )
    if not items:
        items.append(
            _current_parent_only_row(
                parent=parent,
                identifier="purchase_order",
                parent_fields=parent_fields,
            )
            | {
                "ordered_stock_qty": None,
                "received_stock_qty": None,
                "open_receipt_stock_qty": None,
            }
        )
    return ToolResult(
        items=items[offset : offset + limit + 1],
        source_modified_at=_latest_modified([parent, *lines]),
    )


@register(
    name="demand.open",
    version="1",
    required_doctypes=("Material Request", "Item", "Warehouse"),
    input_fields={"item_code": _optional("item_code"), "warehouse": _optional("warehouse")},
    timeout_ms=8_000,
)
def open_demand(run: RunContext, tool_input: dict[str, JsonScalar]) -> ToolResult:
    offset, limit = _page(tool_input)
    item_code = cast(str | None, tool_input["item_code"])
    requested_warehouse = cast(str | None, tool_input["warehouse"])
    warehouses = _warehouse_names(run, requested_warehouse)
    if item_code:
        _ensure_enabled_item(run, item_code)

    parents = _open_material_requests(run, MAX_OPEN_DOCUMENTS + 1)
    if len(parents) > MAX_OPEN_DOCUMENTS:
        raise GatewayFault("RESULT_LIMIT", "open demand source is too large", 422)
    parent_names = [row.name for row in parents]
    if not parent_names or not warehouses:
        return ToolResult(items=[])
    filters: dict[str, Any] = {
        "parent": ["in", parent_names],
        "warehouse": ["in", warehouses],
    }
    if item_code:
        filters["item_code"] = item_code
    lines = frappe.get_list(
        "Material Request Item",
        fields=[
            "parent",
            "item_code",
            "item_name",
            "warehouse",
            "stock_uom",
            "stock_qty",
            "ordered_qty",
            "schedule_date",
            "modified",
        ],
        filters=filters,
        parent_doctype="Material Request",
        user=run.initiator,
        order_by="item_code asc, warehouse asc, parent asc",
        limit=MAX_DEMAND_LINES + 1,
    )
    if len(lines) > MAX_DEMAND_LINES:
        raise GatewayFault("RESULT_LIMIT", "open demand source is too large", 422)
    if not lines:
        return ToolResult(items=[])
    enabled_codes = _enabled_item_codes(run, lines)
    inactive_lines = sum(line.item_code not in enabled_codes for line in lines)
    aggregate: dict[tuple[str, str, str], dict[str, JsonScalar]] = {}
    requests_by_key: dict[tuple[str, str, str], set[str]] = {}
    included_lines: list[Any] = []
    for line in lines:
        outstanding = _quantity(line.stock_qty) - _quantity(line.ordered_qty)
        if outstanding <= 0 or line.item_code not in enabled_codes:
            continue
        included_lines.append(line)
        key = (line.item_code, line.warehouse, line.stock_uom)
        current = aggregate.setdefault(
            key,
            {
                "item_code": line.item_code,
                "item_name": line.item_name,
                "warehouse": line.warehouse,
                "stock_uom": line.stock_uom,
                "open_stock_qty": "0",
                "material_request_count": 0,
                "earliest_schedule_date": str(line.schedule_date),
            },
        )
        current["open_stock_qty"] = _quantity_text(
            _quantity(current["open_stock_qty"]) + outstanding
        )
        requests_by_key.setdefault(key, set()).add(line.parent)
        current["material_request_count"] = len(requests_by_key[key])
        current["earliest_schedule_date"] = min(
            str(current["earliest_schedule_date"]), str(line.schedule_date)
        )
    rows = [aggregate[key] for key in sorted(aggregate)]
    scoped_parent_names = {name for names in requests_by_key.values() for name in names}
    scoped_parents = [row for row in parents if row.name in scoped_parent_names]
    modified = _latest_modified([*scoped_parents, *included_lines])
    omissions = {"inactive_item_lines": inactive_lines} if inactive_lines else {}
    return ToolResult(
        items=rows[offset : offset + limit + 1],
        source_modified_at=modified,
        omissions=omissions,
    )
