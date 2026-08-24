# Phase 1 P2P 源码地图（候选基线取证）

- 日期：2026-08-24
- 对应：`docs/PLAN.md` P1.4；前置证据见 `docs/development-log/2026-08-24-phase1-inc3-manual-p2p.md`
- 固定候选 SHA（正式冻结待 P1.5 独立对抗审查后由 ADR-0002 记录）：
  - Frappe：`6a329d068416768ec47ccd3326b9cc95a8d7bf99`
  - ERPNext：`11e0ba0a1c45f217e2e73e885f699102d06da325`
- 取证方式：容器内只读读取固定版本源码 + 候选 site 只读查询 + P1.3 运行证据；本任务未修改上游、未直写数据库、未 reset/cleanup 任何 site/volume。

## 分类法

本地图每条结论都带标签，禁止混用：

| 标签 | 含义 |
| --- | --- |
| `[S]` 源码事实 | 直接从固定 SHA 源码/官方测试读到的内容，含行号 |
| `[R]` 运行观察 | 在候选 site 上只读查询或 P1.3 实际运行观察到的事实 |
| `[P]` 产品策略 | `docs/PRD.md` / `docs/SPEC.md` / `docs/ARCHITECTURE.md` 已批准的策略 |
| `[I]` 推断 | 由源码/观察合理推导但未逐行验证的内容，标注依据 |
| `[U]` 未决项 | 仍需企业配置或用户决策，不能由实现 Agent 自行补全 |

## 1. 核心对象（DocType）与 Controller

| DocType | DocType JSON（固定 SHA） | Controller 关键位置 |
| --- | --- | --- |
| Material Request | `apps/erpnext/erpnext/stock/doctype/material_request/material_request.json` | `material_request.py` |
| Purchase Order | `apps/erpnext/erpnext/buying/doctype/purchase_order/purchase_order.json` | `purchase_order.py` |
| Purchase Receipt | `apps/erpnext/erpnext/stock/doctype/purchase_receipt/purchase_receipt.json` | `purchase_receipt.py` |
| Purchase Invoice | `apps/erpnext/erpnext/accounts/doctype/purchase_invoice/purchase_invoice.json` | `purchase_invoice.py` |

- `[S]` docstatus 语义为 Frappe 核心约定：`0=Draft`、`1=Submitted`、`2=Cancelled`。
- `[S]` MR 状态机：`update_status(self, status)`（`material_request.py:308`）先 `status_can_change` 校验（Cancelled 单据不可再变，Draft 只允许指定转换，`material_request.py:320-330`），再 `set_status` 并 `update_requested_qty`；模块级 `update_status(name, status)`（`:554`）供外部调用。
- `[S]` PO 状态值在 `purchase_order.py:141-144` 声明（`To Receive and Bill`、`To Bill`、`Completed` 等），状态由 `update_status`（`:430`）与 `update_status_updater`（`:507`）派生维护。
- `[S]` PR 状态值在 `purchase_receipt.py:130-131`（`To Bill`、`Completed`），全量开票后 `:388` 将状态置为 `Completed`，另有 `update_status`（`:963`）。
- `[S]` PI 状态值在 `purchase_invoice.py:188` 附近（含 `Unpaid`），outstanding 派生逻辑与 `update_status_updater_args`（`:709`）相关。
- `[R]` P1.3 最终四单据均为 `docstatus=1`（Submitted）；状态分别为 MR `Received`、PO `Completed`、PR `Completed`、PI `Unpaid`。

## 2. 转换链：MR → PO → Receipt → Invoice

### 2.1 MR → PO
- `[S]` `make_purchase_order(source_name, target_doc=None, args=None)`（`material_request.py:561`）：通过 mapper 生成 PO；`args` 支持 `requested_qty` 子集（`get_source_item_for_qty`）、`supplier`、`filtered_children`；`postprocess` 设置 `is_subcontracted` 与 supplier 后调用 `set_missing_values`。
- `[S]` 官方测试：`test_material_request.py:41` `test_make_purchase_order`；`:1228` `test_make_purchase_order_sets_supplier`；`:1234`/`:1264`/`:1288` 供应商分组转换及其 schedule date/非法行校验。
- `[R]` P1.3 运行观察：Buyer 创建并提交 MR `MAT-MR-2026-00009`；Buyer 生成 PO、独立 Approver 提交 `PUR-ORD-2026-00009`，状态 `Completed`、币种 CNY。

### 2.2 PO → PR
- `[S]` `make_purchase_receipt(source_name, target_doc=None, args=None)`（`purchase_order.py:761`）：按 `qty - received_qty` 计算剩余收货数量（qty 在 `:773`、`stock_qty` 在 `:774`），`amount` 同比例（`:775`）；`is_unit_price_row` 时按单价行处理（`:769-770`）。
- `[S]` 官方测试：`test_purchase_order.py:86` `test_make_purchase_receipt`；`:647` `test_purchase_order_invoice_receipt_workflow`（PO→PR→PI 整链）；`:676` `test_make_purchase_invoice`；`:1539` `test_purchase_order_over_billing_missing_item`。
- `[R]` P1.3 运行观察：Receiver（Stock User + Purchase User）创建并提交 PR `MAT-PRE-2026-00007`，状态 `Completed`、币种 CNY、`per_billed=100.0`。

### 2.3 PR → PI
- `[S]` `make_purchase_invoice(source_name, target_doc=None, args=None)`（`purchase_receipt.py:1509`）：读取 `returned_qty_map` / `invoiced_qty_map` 计算可开票数量；若 items 为空则 `frappe.throw(_("All items have already been Invoiced/Returned"))`（`:1521-1523`）。
- `[S]` 官方测试：`test_purchase_receipt.py:131` `test_make_purchase_invoice`；`:706` `test_pr_billing_status`；`:1121`/`:1147` 退货数量与重复 items 场景；`:6417` `test_cancel_blocked_by_submitted_invoice_rolls_back`（已开票 PR 的取消被阻止并回滚）。
- `[R]` P1.3 运行观察：Accountant 创建并提交 PI `ACC-PINV-2026-00005`，状态 `Unpaid`、币种 CNY、outstanding=500.0；未创建 Payment Entry（P1.3 边界）。

## 3. 权限矩阵（固定版本标准 DocPerm）

- `[R]` 候选 site 只读查询（`frappe.get_all("DocPerm", filters={"parent": dt})`，seed/p2p 脚本未修改任何 DocPerm/Workflow）得到的默认权限：

| Role | Material Request | Purchase Order | Purchase Receipt | Purchase Invoice |
| --- | --- | --- | --- | --- |
| Purchase User | 全权（含 submit/cancel/amend/delete） | 全权 | 全权 | 只读 |
| Purchase Manager | 全权 | 全权（另有 level 1 只读+写） | — | — |
| Stock User | 全权 | 只读 | 全权 | — |
| Stock Manager | 全权 | — | 全权（另有 level 1 只读+写） | — |
| Accounts User | — | — | 只读 | 全权（delete=0） |
| Accounts Manager | — | — | — | 全权（另有 level 1 只读+写） |
| Auditor | — | — | — | 只读 |

- `[R]` 关键观察：默认 DocPerm 允许**同一个 Purchase User 创建并提交 PO**；PO 对 Stock User 只读；PI 对 Purchase User 只读；Accounts User 对 PR 只读。
- `[R]` P1.3 失败路径实测（HTTP）：Viewer（无业务角色）创建 MR → 403 `PermissionError`；Accountant 读取 PO → 403 `PermissionError`；Receiver 仅 Stock User 时创建 PR 因无法读取 Account 被拒（Inc-3 卡点 4），补充 Purchase User 后通过——未绕过权限，是按其上游 DocPerm 需求补足最小角色。
- `[P]` Synora 策略：继承 ERPNext 权限与 Workflow，不在 Agent Runtime 复制审批规则（`PRD.md` 5.5、`ARCHITECTURE.md` "Approval and Workflow Authority"）；规则缺失、冲突或无法验证时 fail closed。
- `[P]` 审批基线（`PRD.md:232`、`SPEC.md:256-261`）：MR Draft 与 PO Draft 由发起人显式确认即可执行；PO Submit、Purchase Receipt、Purchase Invoice 与 Payment 相关写操作必须由**不同于发起人**的有权审批人授权；始终采用 ERP Workflow 与 Synora 策略中更严格者。默认 DocPerm 允许单用户全链操作是 ERPNext 出厂行为，不代表审批策略已解决。
- `[U]` 具体企业 Workflow 状态、多级审批、金额阈值与角色→Workflow 映射（`approval-workflow-mapping`）仍需用户决定，最迟 Phase 4 启用写入前完成。

## 4. Workflow 观察

- `[R]` 候选 site 只读查询 `frappe.get_all("Workflow")` 返回空列表——MR/PO/PR/PI 均未启用 Workflow，`Workflow` DocType 无记录。
- `[I]` 依据：P1.3 中 Buyer/Approver 等角色可直接经标准 HTTP API 提交四类单据、无 Workflow 状态字段出现，与"无 Workflow"一致（推断依据：提交路径无 workflow 校验错误；未逐行追踪 frappe Workflow 钩子）。
- `[P]` "当前 site 无 Workflow"是运行观察，不是审批策略已解决：企业 Workflow 与多级审批仍属 `[U]`，见第 3 节。
- `[U]` 是否在候选 site 上为四类单据启用 Workflow 以验证 ERP Workflow 门禁，属于企业配置决策，不在本阶段擅自更改（本阶段禁止修改 site 配置中的策略性内容）。

## 5. 业务不变量

### 5.1 币种链
- `[S]` `set_price_list_currency(buying_or_selling)`（`accounts_controller.py:1017`）：单据带 buying/selling price list 时，将 `price_list_currency` 置为 Price List 币种；与公司币种相同时 `plc_conversion_rate=1.0`，不同时经 `get_exchange_rate` 换算。
- `[S]` `validate_party_account_currency`（`accounts_controller.py:2515`）：PI/SI 的往来科目币种（PI 为 `credit_to`）须与单据币种一致，否则拒绝；`allow_multi_currency_invoices_against_single_party_account`（Accounts Settings）可放宽但需显式配置。
- `[S]` 首个 Buying Price List 默认行为：`price_list.py:35` `on_update` → `:40` `set_default_if_missing`——若 Buying Settings 尚无 `buying_price_list`，该 Price List 会被自动设为 Buying 默认（selling 同理）。
- `[R]` 候选 site 当前配置：`Global Defaults.default_currency=CNY`、`Buying Settings.buying_price_list=SYNORA-P1 Buying CNY`；P1.3 的 PO/PR/PI 均为 CNY。Inc-2 日志记录 Global Defaults 与 Buying Price List 的 seed 对齐过程。

### 5.2 会计年度
- `[S]` `FiscalYearError(frappe.ValidationError)`（`accounts/utils.py:51`），在 `get_fiscal_years` 找不到有效会计年度时抛出（`:141`）。
- `[R]` Inc-3 卡点 1：空 site 无 Fiscal Year 时 MR 提交被 `FiscalYearError` 拒绝；seed 补 `SYNORA-P1 FY 2026` 后通过。

### 5.3 提交后字段不可变
- `[S]` `_validate_update_after_submit`（`frappe/model/base_document.py:1270`）：`allow_on_submit=False` 的字段在提交后修改被拒绝（与 DB 现值逐字段比对）。
- `[R]` P1.3 失败用例 F4：Buyer 修改已提交 MR 的 `transaction_date` → HTTP 417 `UpdateAfterSubmitError`。

### 5.4 超收 / 超开票
- `[S]` 通用允许率读取：`status_updater.py:423-425` 将全局 `over_delivery_receipt_allowance`（Stock Settings）作为 qty 允许率默认值，`get_allowance_for`（`:754`，读取 Item 级 `over_delivery_receipt_allowance` / `over_billing_allowance`，`:796-805`）。
- `[S]` 内部调拨路径：`stock_controller.py:1776` 在 `validate_internal_transfer_qty` 内读取同一 `over_delivery_receipt_allowance`，超量则 throw（针对 PR/PI 的内部调拨场景）。
- `[S]` 超开票：`validate_multiple_billing`（`accounts_controller.py:2209`）计算 `ref_amt*(100+allowance)/100` 上限；超开票豁免角色来自 `Accounts Settings.role_allowed_to_over_bill`（读取行 `:2222`）。
- `[R]` 候选 site 当前配置：`over_delivery_receipt_allowance=0.0`、`role_allowed_to_over_bill=""`（默认不允许任何角色超开票）。

### 5.5 数量不变量
- `[S]` PO→PR 剩余数量按 `qty - received_qty` 计算（`purchase_order.py:773`，amount 在 `:775`），防止重复收货超过 PO 数量（叠加 5.4 允许率）。
- `[S]` PR→PI 拒绝已全部开票/退货的 items（`purchase_receipt.py:1521-1523`）。
- `[R]` P1.3 固定输入：qty=5、rate=100 CNY → PO/PR 金额 500、PI outstanding=500.0，链上数量一致。

### 5.6 取消 / 回滚
- `[S]` 官方测试 `test_purchase_receipt.py:6417` 证明：已开票的 PR 取消会被阻止并回滚。
- `[S]` PI 取消后 outstanding/退款相关行为有官方测试：`test_purchase_invoice.py:1139`/`:1172`（预付款凭证/付款单取消后的 outstanding 重算）、`:3271`（会计冻结日期后取消被拒）。
- `[I]` PI 取消回滚的具体实现位于 accounts 控制器与状态更新链（`accounts_controller.py` / `status_updater.py` 的 `update_status` 相关逻辑），本阶段未逐行追完；P1.3 未执行取消操作，取消路径的行为证据以官方测试为准。

## 6. 代表性官方测试（固定 SHA）

| 文件 | 行号 | 测试 | 覆盖 |
| --- | --- | --- | --- |
| `stock/doctype/material_request/test_material_request.py` | 41 | `test_make_purchase_order` | MR→PO 转换 |
| 同上 | 1228 | `test_make_purchase_order_sets_supplier` | 转换时设置供应商 |
| 同上 | 1234 / 1264 / 1288 | 供应商分组转换 / schedule date / 非法行 | MR→PO 批量与校验 |
| `buying/doctype/purchase_order/test_purchase_order.py` | 86 | `test_make_purchase_receipt` | PO→PR 转换 |
| 同上 | 647 | `test_purchase_order_invoice_receipt_workflow` | PO→PR→PI 整链 |
| 同上 | 676 | `test_make_purchase_invoice` | PO→PI 直接转换 |
| 同上 | 1539 | `test_purchase_order_over_billing_missing_item` | 超开票边界 |
| `stock/doctype/purchase_receipt/test_purchase_receipt.py` | 131 | `test_make_purchase_invoice` | PR→PI 转换 |
| 同上 | 706 | `test_pr_billing_status` | 开票状态派生 |
| 同上 | 1121 / 1147 | 退货数量 / 重复 items | 部分退货开票 |
| 同上 | 6417 | `test_cancel_blocked_by_submitted_invoice_rolls_back` | 取消回滚 |
| `accounts/doctype/purchase_invoice/test_purchase_invoice.py` | 1139 / 1172 | 预付款凭证 / 付款单取消 | outstanding 重算 |
| 同上 | 3136 | `test_pr_pi_over_billing` | PR→PI 超开票 |
| 同上 | 3271 | `test_purchase_invoice_cancellation_post_account_freezing_date` | 会计冻结日期 |

## 7. 结论分类汇总

**`[S]` 源码事实**：转换三函数（MR→PO `material_request.py:561`、PO→PR `purchase_order.py:761`、PR→PI `purchase_receipt.py:1509`）及其数量/金额计算（`purchase_order.py:773-775`）；四 DocType JSON 与状态派生位置；币种链（`accounts_controller.py:1017,2515`、`price_list.py:40`）；Fiscal Year（`accounts/utils.py:51,141`）；提交后字段不可变（`base_document.py:1270`）；超收/超开票（`status_updater.py:423-425,754,796-805`、`stock_controller.py:1776`、`accounts_controller.py:2209,2222`）；官方测试 14 处（第 6 节）。

**`[R]` 运行观察**：候选 site 无任何 Workflow；四类单据默认 DocPerm 矩阵（第 3 节）；`Global Defaults=CNY`、Buying Price List、允许率配置；P1.3 四单据最终状态（docstatus=1，PO/PR/PI CNY，PI `Unpaid` outstanding=500.0）；失败路径 403/417 实测。

**`[P]` 产品策略**：继承 ERPNext 权限/Workflow、Runtime 不复制审批规则；MR/PO Draft 发起人显式确认、PO Submit 及后续 P2P 写操作独立审批人（`PRD.md:232`、`SPEC.md:256-261`）；规则缺失/冲突/无法验证 fail closed。

**`[I]` 推断**：site 无 Workflow 与 P1.3 无 Workflow 错误一致（依据：提交路径观察，未逐行追踪 frappe Workflow 钩子）；PI 取消回滚实现位置（依据：官方测试覆盖，未逐行追完）。

**`[U]` 未决项**：企业 Workflow 状态/多级审批/金额阈值/角色→Workflow 映射（`approval-workflow-mapping`）；正式 commit pair 冻结（`erp-version-pair`，P1.5 解决）；候选 site 是否启用 Workflow 属企业配置决策。

## 8. 限制与后续

- 本地图基于**候选 SHA**，按 ADR-0001 约束，冻结前不得用于结论性证据之外的用途；P1.5 独立对抗审查通过后由 ADR-0002 正式冻结。
- 所有行号以固定 SHA 为准；冻结后若更换版本必须重新取证。
- 本阶段未执行取消/退货路径的真实运行，取消行为以官方测试为证据源。
- 未修改任何上游源码、未直写数据库、未 reset/cleanup 当前 site/volume；seed 与 p2p 脚本只创建主数据、用户与业务单据。
