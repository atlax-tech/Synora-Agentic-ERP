# ERPNext P2P 对象与转换 (curated 摘要)
source_type: source-map
revision: v1
permission_scope: internal
---

## 核心对象 (固定 SHA: frappe 6a329d0 / erpnext 11e0ba0)
- Material Request (stock/doctype/material_request) — 需求单据
- Purchase Order (buying/doctype/purchase_order) — 采购订单
- Purchase Receipt (stock/doctype/purchase_receipt) — 收货单
- Purchase Invoice (accounts/doctype/purchase_invoice) — 发票
- docstatus: 0=Draft 1=Submitted 2=Cancelled (Frappe 核心约定)

## 转换链
- MR → PO: make_purchase_order (material_request.py:561), 支持 requested_qty 子集与供应商
- PO → PR: make_purchase_receipt (purchase_order.py:761), 剩余数量 = qty - received_qty
- PR → PI: make_purchase_invoice (purchase_receipt.py:1509), 已全部开票/退货时拒绝

## 关键不变量
- 币种: price_list_currency 与公司币种一致时 plc_conversion_rate=1.0, PI 往来科目币种须与单据一致
- 会计年度: 无有效 Fiscal Year 时 MR 提交被 FiscalYearError 拒绝
- 提交后字段不可变: allow_on_submit=False 的字段修改被拒 (base_document.py:1270)
- 超收/超开票: over_delivery_receipt_allowance 默认 0.0, 超开票需 role_allowed_to_over_bill

来源: docs/source-maps/phase1-p2p-source-map.md (完整证据含行号与官方测试)
