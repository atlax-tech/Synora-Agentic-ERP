# Execute — Step 006：交付 PO Draft 与高风险 UI

## 单一任务

在 MR Draft 闭环、幂等和对账已通过的基础上，将相同治理骨架精确扩展到单个 `CREATE_PO_DRAFT`，并在 Frappe Runs 页面提供可访问的 proposal/风险/审批/执行/Receipt/对账交互；仍不提交 PO，不做批量写。

## 先读

- Steps 001–005 最终 diff、真实证据和独立 `PASS`。
- `docs/PRD.md#54-f-004-可解释计划与-proposedaction` 至 `#58-f-008-audit--trace--failure-evidence`。
- `docs/DESIGN.md#High-Risk Action Design`、`#State and Feedback Contract`、`#Accessibility and Responsive Boundary`、双语 glossary。
- `docs/SPEC.md#9-tool-gateway-specification` 至 `#11-idempotency-and-reconciliation`。
- 固定 ERPNext `purchase_order.json`/`purchase_order.py`、Item Price/Buying Settings 相关 controller 和官方 PO tests；确认 Draft 必填字段、价格/币种/UOM/schedule/duplicate 行为。
- 当前 `runs.js`、`new_run.js`、Run/list/get API 与 UI tests。
- 编码前 `ponytail` full。

## 当前事实

- `CONFIRMED`：PO `docstatus=0` 是本阶段上限，PO Submit 不可达。
- `CONFIRMED`：供应商、数量、单价、金额、币种、UOM、日期和重复风险必须由 ERP/确定性代码生成并绑定 proposal digest，模型不能估算。
- `CONFIRMED`：MR 与 PO 是两种独立 action；不能假设 Phase 6 创建的未提交 MR 可直接成为 PO 转换源。
- `CONFIRMED`：页面显示的成功必须来自 read-back Receipt；超时/断连显示“需要对账”，不能显示“重试执行”。

## 改动边界

- 允许：PO typed preview/payload/checks/executor/read-back；按 action type 复用已证明的 reservation/reconciliation；proposal/approval/execute/status API serializers；Runs 页面 proposal/decision/Receipt/reconciliation UI；JS/Python/Frappe/HTTP/browser tests；Phase 6 日志。
- 禁止：PO Submit、MR→PO 未提交单据转换假设、模型计算价格/金额、用户在批准后编辑 payload、批量 PO/部分成功、generic write、客户端 digest/permission 权威、任意自定义视觉体系、移动端/离线扩张、上游/README/学习笔记/Harness 手改。

## 执行

1. Context Receipt 列出 PO business source、supplier/item/price/amount/schedule snapshot、预期单据数、UI states 和浏览器矩阵。
2. 先固定上游证据，选择最小合法 PO Draft 构造路径：直接 typed PO payload，或仅当 source MR 已提交且 controller 明确支持时使用官方 mapper。若两种都有实质成立依据并影响公共契约，停止并提交选择证据，不擅自发明。
3. Proposal 阶段在 Frappe 内确定性 preview：读取 supplier/item enabled、purchase UOM、price list/currency/rate、qty、schedule、warehouse/company、tax/total（本阶段实际涉及的关键字段）；调用标准 controller 的无持久化计算能力或固定算法，生成 reviewed critical fields 和 snapshot。
4. PO action schema 明确 supplier、company、currency、items、qty/UOM/rate/amount/schedule/warehouse、source evidence、duplicate-open-PO check；未知/缺失/冲突/0 或负数量、不可解释 rate/amount fail closed。
5. 执行前重新 preview/recheck；任一 supplier/item/price/currency/UOM/qty/schedule/duplicate/permission/Workflow 差异使旧 action 过期，不能用新值执行旧 approval。
6. 复用 Step 004/005 的 T1 reservation、T2 controller+read-back+Receipt 与 reconciliation，但通过明确 action dispatch，只允许 MR/PO 两个专用 writer；不引入 generic DocType writer。
7. 标准 controller 创建 PO Draft；read-back 至少核对 `docstatus=0`、supplier/company/currency/items/qty/UOM/rate/amount/schedule/warehouse 和实际固定版本要求的 critical fields。PO Submit API/tool/按钮不存在。
8. Runs 页面每个 proposal 显示：目标、action/后果、ERP 数据来源、关键计算、supplier/item/qty/amount、risk/unknown、snapshot time、expiry、approval rule、digest 短标识；权限不足不显示详情。
9. UI 状态完整：loading、empty、schema/policy rejected、awaiting、approved/executing、declined、changes requested、expired、succeeded、failed、reconciliation required、manual intervention；每种给明确下一步和 correlation。
10. 按钮：确认文案明确“将创建 1 份 MR/PO Draft，执行前再校验”；confirm/decline/changes requested 禁重复、键盘可用、可见焦点、aria-live；执行请求发送 immutable action reference/digest，不发送 editable business payload。
11. Receipt 展示 target DocType/name、verified critical fields、actor、time、correlation；提供有权限的 ERP 单据链接。对账状态只允许 status/reconcile read action，不显示“重新执行”。
12. UI 所有业务文本用 `frappe.utils.escape_html` 或等价安全渲染，恶意 ERP/LLM 字段不成为 HTML；长 trace 分段/折叠，状态不只靠颜色。
13. 测试 PO 正常、permission/Workflow、supplier/item disabled、price/amount/currency/UOM/schedule drift、duplicate PO、same/different digest、response loss/reconcile、并发；浏览器覆盖正常/失败/权限/过期/拒绝/修改/对账和键盘/aria。
14. targeted → real ERP HTTP → 登录态浏览器 → wider checks → ponytail-review → 独立 Test/Review；修复后更新日志并提交。

## 问题发现与修复

- preview 与 controller read-back 金额不同：先查固定 ERP price/tax/currency/UOM 规则；旧 proposal 过期，修正 deterministic preview 或收窄本阶段支持条件，不允许容差吞掉业务差异。
- 缺可靠价格来源：返回 NEEDS_INPUT/UNSUPPORTED 并停止 PO 执行；不要用模型或 0 价格补齐。
- 用户在 UI 改表单后执行：审批面板不得是可编辑业务表单；“要求修改”创建新 proposal，不修改批准 payload。
- UI 成功但 Receipt/ERP 不一致：以服务端 status/read-back 为准，立即撤掉成功态并进入对账/失败。
- XSS/权限过滤失败：视为 P1，停止阶段出口，修所有渲染/serializer/visibility 路径并浏览器复验。
- PO 复用导致 generic writer：收回到两个显式 action handlers 和共享的小型安全 primitive。

## 验证与证据

- Unit：PO schema/preview/digest/recheck/read-back；UI helper escaping/state mapping。
- Frappe integration/HTTP：真实 PO controller、permission/Workflow/validation、idempotency/reconciliation、audit/Receipt。
- Browser：登录态 Buyer 与无权用户；所有 UI states、重复点击、keyboard/focus/aria、XSS、ERP link。
- Architecture：PO Submit/后续 P2P/generic writer/Runtime writer 不可达。
- 提供 PO/MR names、count、read-back、screenshots或 DOM 证据、命令/退出码、浏览器/环境限制。

## 提交纪律

建议：`feat(phase6): deliver purchase order draft review flow`。PO backend、该用户结果所需 UI 与测试同一主提交；不拆成无可见结果的骨架提交。

## 最终报告

按大白话五项交付：业务问题、用户可见结果、数据流、三个关键文件、手工验证；再列命令/退出码、MR/PO/Receipt/对账证据、限制和 Step 007 前置。
