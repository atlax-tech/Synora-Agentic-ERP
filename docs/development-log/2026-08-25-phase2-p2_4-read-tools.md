# Phase 2 P2.4：六个真实 ERP 只读工具

- 日期：2026-08-25
- 需求：PRD F-002；PLAN P2.4；SPEC §9。

## 改动与业务口径

在固定 ERPNext `11e0ba0` 源码口径上注册 Item、Supplier、projected stock、open demand、open Material Request、open Purchase Order 六个 READ 工具。所有查询使用 Frappe `get_list` 并显式传入 Run initiator，强制公司/仓库范围、上游 DocType 权限、停用主数据排除、稳定排序、分页与结果上限；没有直接 SQL、`get_all` 或 ERPNext 内部 import。

`open demand` 明确定义为已提交、Purchase 类型、未 Stopped/Cancelled、`per_ordered < 99.99` 的 Material Request 行，按固定源码的 `stock_qty - ordered_qty` 计算未订购库存单位数量，再按 Item/Warehouse/UOM 聚合。open MR/PO 同样保持 Item/Warehouse/stock UOM 维度，不把不同单位相加；PO 按上游公式 `(qty - received_qty) * conversion_factor` 统一为库存单位，数量以十进制定点字符串输出。open PO 只包含上游状态 `To Receive and Bill`、`To Receive`、`To Bill`。停用主数据不会进入结果或 snapshot，同时通过 `completeness=PARTIAL` 和分类计数明确标记省略，避免被误当成零。

固定 SHA 源码锚点：Item `item.json:156,205,222`；Supplier `supplier.json:99,214`；Bin `bin.py:78-88` / `bin.json:112`；MR `material_request.py:421,850`、`material_request_item.json:76,167,177,221,348`、官方测试 `test_material_request.py:356,600`；PO `stock_balance.py:206`、`purchase_order.py:774`、`purchase_order_item.json:228,258,461,627`、官方测试 `test_purchase_order.py:86`。行号均以 ERPNext `11e0ba0` 为准。

## 验证

实际 `format-check`、`lint`、`type`、`unit` 通过（unit 5 passed）；`integration` 通过（Bench App 22 passed）。集成测试使用现有真实 Company/Item/Supplier/Warehouse/Bin，并经标准 DocType API 创建和提交开放 MR/PO，覆盖 Accountant 权限不扩大、权限依赖声明、同公司跨仓库结果与 snapshot 隔离、停用供应商显式省略、混合库存单位换算和分页上限。独立 Test/Review/Ponytail 结论在提交门禁中记录。

## 限制与手工验收

本增量只读，不创建 Runtime client。用 Buyer Run 依次调用六个工具应返回版本化 READ envelope 和固定源码 snapshot；Accountant 调 open PO、Run 限定仓库后请求其他仓库必须得到脱敏拒绝。
