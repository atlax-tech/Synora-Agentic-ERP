# Test — Step 006：独立验证 PO Draft 与高风险 UI

独立核对真实 PO、Receipt、对账与浏览器行为。不要修 backend/UI。

## 需求来源

- PRD F-004–F-008 与第一受控写入验收。
- `docs/DESIGN.md#High-Risk Action Design`、前端 acceptance checks。
- SPEC 9–11、14。

## 行为矩阵

- 正常：有权用户看到完整 PO proposal，确认后只创建一份 `docstatus=0` PO，read-back/Receipt/UI 一致。
- 错误：无权/未审批/过期/price-or-state drift/disabled supplier-item/duplicate/validation/response loss，安全失败或进入对账，不显示误导成功。
- 边界：same/different digest、并发点击、慢网、刷新/返回、恶意 supplier/item/LLM 文本、键盘-only、aria、1280px、权限撤销、Receipt link。

## 测试范围

- Unit：PO business fields、金额/币种/UOM、state copy/escaping。
- Integration/HTTP：真实 controller/permission/Workflow、recheck、read-back、idempotency/reconciliation。
- Browser：Buyer、Viewer/跨用户；正常/空/加载/失败/过期/拒绝/修改/对账；Tab/Enter/Space/focus/aria-live；双击只一请求；XSS payload 只显示文本。
- Architecture：PO Submit 和后续 P2P endpoints/tools/buttons 均不存在；Runtime/generic writer 不可达。
- Manual：从 proposal→confirm→ERP PO Draft→Receipt→replay；再做一个 lost-response 场景并从 UI 进入对账。

## 失败证据

保留浏览器网络/DOM/截图、actor/scope、action/digest、PO count/name/read-back、Receipt/reconciliation、console/server logs 和命令/退出码；不清理不确定单据。

## 判定

真实 PO、负面安全集与浏览器高风险交互全部通过且无 Submit/重复/越权/XSS 时 `PASS`；否则 `FAIL/BLOCKED`。
